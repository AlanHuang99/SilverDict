import struct
import bisect
import zlib
import os
from pathlib import Path
import pickle
import io
try:
	import lzo
	lzo_is_c = True
except ImportError:
	from .mdict import lzo
	lzo_is_c = False
import concurrent.futures
from .base_reader import BaseReader
from .mdict import MDX, MDD, HTMLCleaner
from .. import db_manager
from ..resource_paths import resource_path, copy_assets
from ..utils import run_in_thread_pool
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class MDictReader(BaseReader):
	FILENAME_MDX_PICKLE = 'mdx.pickle'

	def _write_to_cache_dir(self, resource_filename: str, data: bytes) -> None:
		# Finder metadata can be bundled in an MDD but is not dictionary content.
		if resource_filename.replace('\\', '/').rsplit('/', 1)[-1] == '.DS_Store':
			return
		absolute_path = resource_path(self._resources_dir, resource_filename)
		directory = Path(os.path.dirname(absolute_path))
		directory.mkdir(parents=True, exist_ok=True)
		with open(absolute_path, 'wb') as f:
			f.write(data)

	def __init__(self,
				 name: str,
				 filename: str,
				 display_name: str,
				 extract_resources: bool = True,
				 remove_resources_after_extraction: bool = False,
				 load_content_into_memory: bool = False) -> 'None':
		"""
		It is recommended to set remove_resources_after_extraction to True on a server when you have local backup.
		"""
		super().__init__(name, filename, display_name)
		filename_no_extension, extension = os.path.splitext(filename)
		self._resources_dir = os.path.join(self._CACHE_ROOT, name)
		Path(self._resources_dir).mkdir(parents=True, exist_ok=True)

		filename_mdx_pickle = os.path.join(self._resources_dir, self.FILENAME_MDX_PICKLE)
		if os.path.isfile(filename_mdx_pickle):
			mdx_pickled = True
			with open(filename_mdx_pickle, 'rb') as f:
				self._mdict = pickle.load(f)
			if self._mdict._fname != filename: # the pickle's off
				self._mdict = MDX(filename)
				mdx_pickled = False
		else:
			mdx_pickled = False
			self._mdict = MDX(filename)

		if not db_manager.dictionary_exists(self.name):
			# Cached readers omit key lists after indexing. A retried import may
			# have removed its partial DB index while retaining this cache.
			if not hasattr(self._mdict, '_key_list'):
				self._mdict = MDX(filename)
				mdx_pickled = False
			db_manager.drop_index()
			for i in range(len(self._mdict._key_list)):
				offset, key = self._mdict._key_list[i]
				if i + 1 < len(self._mdict._key_list):
					length = self._mdict._key_list[i + 1][0] - offset
				else:
					length = -1
				db_manager.add_entry(self.simplify(key.decode('UTF-8')),
						 			 self.name,
									 key.decode('UTF-8'),
									 offset,
									 length)
			db_manager.commit_new_entries(self.name)
			db_manager.create_index()
			logger.info(f'Entries of dictionary {self.name} added to database')

		if not mdx_pickled:
			del self._mdict._key_list # a hacky way to reduce memory usage without touching the library
			with open(filename_mdx_pickle, 'wb') as f:
				pickle.dump(self._mdict, f)

		styles = self._mdict.header.get(b'StyleSheet', b'')
		self.html_cleaner = HTMLCleaner(filename, name, self._resources_dir, styles.decode('utf-8'))

		self._loaded_content_into_memory = load_content_into_memory
		if load_content_into_memory:
			with open(filename, 'rb') as f:
				self._content = io.BytesIO(f.read())

		# If the resources haven't been extracted, then there are the following possible files inside _resource_dir
		# 1. mdx.pickle
		# 2. CSS
		# 3. JS
		if extract_resources and not os.path.isfile(os.path.join(self._resources_dir, '.resources-complete')):
			# Load the resource files (.mdd), if any
			# For example, for the dictionary collinse22f.mdx, there are four .mdd files:
			# collinse22f.mdd, collinse22f.1.mdd, collinse22f.2.mdd, collinse22f.3.mdd
			resources = []
			mdd_base_filename = f'{filename_no_extension}.'
			if os.path.isfile(mdd_filename := f'{mdd_base_filename}mdd')\
				or os.path.isfile(mdd_filename := f'{mdd_base_filename}MDD'):
				resources.append(MDD(mdd_filename))
			i = 1
			while os.path.isfile(mdd_filename := f'{mdd_base_filename}{i}.mdd')\
				or os.path.isfile(mdd_filename := f'{mdd_base_filename}{i}.MDD'):
				resources.append(MDD(mdd_filename))
				i += 1

			# Extract resource files into cache directory
			for mdd in resources:
				for resource_filename, resource_file in mdd.items():
					resource_filename = resource_filename.decode('UTF-8').replace('\\', '/')
					if resource_filename.startswith('/'):
						resource_filename = resource_filename[1:]
					self._write_to_cache_dir(resource_filename, resource_file)

			Path(self._resources_dir, '.resources-complete').touch()

			if remove_resources_after_extraction:
				for mdd in resources:
					os.remove(mdd._fname)

		if os.getenv('SILVERDICT_LIBRARY') == '1':
			copy_assets(Path(filename).parent, self._resources_dir)

	def _get_record(self, mdict_fp, offset: int, length: int) -> str:
		if self._mdict._version >= 3:
			return self._get_record_v3(mdict_fp, offset, length)
		else:
			return self._get_record_v1v2(mdict_fp, offset, length)

	def _get_record_v3(self, f, offset: int, length: int) -> str:
		f.seek(self._mdict._record_block_offset)

		num_record_blocks = self._mdict._read_int32(f)

		decompressed_offset = 0
		for j in range(num_record_blocks):
			decompressed_size = self._mdict._read_int32(f)
			compressed_size = self._mdict._read_int32(f)

			if (decompressed_offset + decompressed_size) > offset:
				break
			decompressed_offset += decompressed_size
			f.seek(compressed_size, 1)

		block_compressed = f.read(compressed_size)
		record_block = self._mdict._decode_block(block_compressed, decompressed_size)

		record_start = offset - decompressed_offset
		if length > 0:
			record_null = record_block[record_start:record_start + length]
		else:
			record_null = record_block[record_start:]

		return record_null.strip().decode(self._mdict._encoding)

	def _record_index_v1v2(self, f):
		"""Validate the complete on-disk record directory before random access."""
		if hasattr(self, '_validated_record_index'):
			return self._validated_record_index
		f.seek(0, os.SEEK_END)
		file_size = f.tell()
		f.seek(self._mdict._record_block_offset)
		try:
			num_blocks = self._mdict._read_number(f)
			num_entries = self._mdict._read_number(f)
			info_size = self._mdict._read_number(f)
			data_size = self._mdict._read_number(f)
		except struct.error as exc:
			raise ValueError('Truncated MDX record header') from exc
		if num_entries != self._mdict._num_entries:
			raise ValueError('MDX record entry count does not match key index')
		if info_size != num_blocks * self._mdict._number_width * 2:
			raise ValueError('Invalid MDX record directory size')
		data_start = f.tell() + info_size
		required_size = data_start + data_size
		if required_size > file_size:
			raise ValueError(f'Truncated MDX record data: requires {required_size} bytes; source has {file_size} bytes')
		blocks = []
		starts = []
		compressed_offset, decompressed_offset = data_start, 0
		for _ in range(num_blocks):
			compressed_size = self._mdict._read_number(f)
			decompressed_size = self._mdict._read_number(f)
			if compressed_size < 8:
				raise ValueError('Invalid MDX compressed record block size')
			starts.append(decompressed_offset)
			blocks.append((compressed_offset, compressed_size, decompressed_size))
			compressed_offset += compressed_size
			decompressed_offset += decompressed_size
		if compressed_offset != required_size:
			raise ValueError('MDX record directory disagrees with declared data size')
		self._validated_record_index = (starts, blocks, decompressed_offset)
		return self._validated_record_index

	def _decode_record_v1v2(self, f, position, compressed_size, decompressed_size):
		f.seek(position)
		block = f.read(compressed_size)
		if len(block) != compressed_size or len(block) < 8:
			raise ValueError('Truncated MDX compressed record block')
		block_type = block[:4]
		checksum = struct.unpack('>I', block[4:8])[0]
		try:
			if block_type == b'\x00\x00\x00\x00':
				record = block[8:]
			elif block_type == b'\x01\x00\x00\x00':
				if lzo_is_c:
					header = b'\xf0' + struct.pack('>I', decompressed_size)
					record = lzo.decompress(header + block[8:])
				else:
					record = lzo.decompress(block[8:], initSize=decompressed_size, blockSize=1308672)
			elif block_type == b'\x02\x00\x00\x00':
				record = zlib.decompress(block[8:])
			else:
				raise ValueError('Unsupported MDX record compression type')
		except zlib.error as exc:
			raise ValueError('Corrupt MDX compressed record data') from exc
		if checksum != zlib.adler32(record) & 0xffffffff:
			raise ValueError('MDX record checksum mismatch')
		if len(record) != decompressed_size:
			raise ValueError('MDX decompressed record size mismatch')
		return record

	def _get_record_v1v2(self, f, offset: int, length: int) -> str:
		starts, blocks, total = self._record_index_v1v2(f)
		if offset < 0 or offset >= total:
			raise ValueError(f'MDX record offset {offset} is outside data range 0..{total}')
		index = bisect.bisect_right(starts, offset) - 1
		# A zero-length location is an upstream duplicate-offset entry; preserve its
		# historical containing-block behavior. -1 denotes the final dictionary entry.
		end = offset + length if length > 0 else (total if length < 0 else starts[index] + blocks[index][2])
		if end > total:
			raise ValueError('MDX record length extends outside data range')
		parts = []
		while index < len(blocks) and starts[index] < end:
			record = self._decode_record_v1v2(f, *blocks[index])
			begin_in_block = max(0, offset - starts[index])
			end_in_block = min(len(record), end - starts[index])
			parts.append(record[begin_in_block:end_in_block])
			index += 1
		# Decode only after joining: a UTF-8/UTF-16 character can cross a block edge.
		return b''.join(parts).strip().decode(self._mdict._encoding)

	def _get_records_in_batch(self, locations: list[tuple[int, int]]) -> list[str]:
		if self._loaded_content_into_memory:
			mdict_fp = self._content
		else:
			with open(self.filename, 'rb') as mdict_fp:
				return [self._get_record(mdict_fp, offset, length) for offset, length in locations]
		return [self._get_record(mdict_fp, offset, length) for offset, length in locations]

	def get_definition_by_key(self, entry: str) -> str:
		locations = db_manager.get_entries(entry, self.name)
		# word is not used in mdict, which is present in the article itself.
		locations = [(offset, length) for word, offset, length in locations]
		records = self._get_records_in_batch(locations)
		# Cleaning up HTML actually takes some time to complete
		records = run_in_thread_pool(self.html_cleaner.clean, records, num_max_workers=len(records))
		return self._ARTICLE_SEPARATOR.join(records)

	def get_definition_by_word(self, headword: str) -> str:
		locations = db_manager.get_entries_with_headword(headword, self.name)
		records = self._get_records_in_batch([(offset, length) for offset, length in locations])
		records = run_in_thread_pool(self.html_cleaner.clean, records, num_max_workers=len(records))
		return self._ARTICLE_SEPARATOR.join(records)
