"""Rewrite dictionary resources for a sandboxed, per-dictionary document."""
from bs4 import BeautifulSoup
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from html import escape

def resource_url(cache, value):
    parts=urlsplit(value)
    return urlunsplit(('', '', cache+quote(unquote(parts.path).lstrip('/'),safe='/'), parts.query, parts.fragment))

def rewrite_article(html, dictionary_name):
    cache = '/api/cache/' + quote(dictionary_name, safe='') + '/'
    lookup = '/api/lookup/' + quote(dictionary_name, safe='') + '/'
    if html.strip().startswith('@@@LINK='):
        word=html.strip()[8:]
        return '<a href="'+lookup+quote(word,safe='')+'">'+escape(word)+'</a>'
    soup=BeautifulSoup(html, 'html.parser')
    for tag in list(soup.find_all(['base','meta','iframe','object','embed','form'])):
        tag.decompose()
    for tag in soup.find_all(True):
        for attr in ('src', 'poster', 'href'):
            value=tag.get(attr)
            if not value or not isinstance(value,str):continue
            value=value.replace('\\','/')
            if value.startswith(('entry://#','bword://#')):
                tag[attr]='#'+value.split('#',1)[1]
            elif value.startswith(('entry://','bword://')):
                tag[attr]=lookup+quote(unquote(value.split('://',1)[1]),safe='#')
            elif value.startswith('sound://'):
                sound=value[len('sound://'):].lstrip('/')
                audio=soup.new_tag('audio', controls='')
                audio['src']=resource_url(cache, sound)
                audio['preload']='none'
                tag.replace_with(audio)
                break
            elif value.startswith(('/api/', 'api/')):
                tag[attr]='/'+value.lstrip('/')
            elif value.startswith('#') or urlsplit(value).scheme in ('http','https','data','javascript'):
                continue
            elif value.startswith('//'):
                continue
            elif attr=='href' and tag.name=='a' and '.' not in value:
                tag[attr]=lookup+quote(unquote(value),safe='#')
            else:
                value=value.removeprefix('file://').lstrip('/')
                tag[attr]=resource_url(cache, value)
    return str(soup)
