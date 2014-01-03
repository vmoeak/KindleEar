#!usr/bin/Python
# -*- coding:utf-8 -*-
"""GAE文件下载类，如果文件太大，则分块下载"""
import urllib, urllib2, Cookie, urlparse, time, re
from google.appengine.api import urlfetch
from google.appengine.runtime.apiproxy_errors import OverQuotaError

URLFETCH_MAX = 2
URLFETCH_MAXSIZE = 1*1024*1024 #最大4M，但是为了稳定性，每次下载1M
URLFETCH_TIMEOUT = 60

def Download(url):
    """ FileDownload工具函数，简化文件下载工作 
    返回一个元组 (status, filename, content)
    """
    
    FilenameFromUrl = lambda url: urlparse.urlparse(url)[2].split('/')[-1]
    
    dl = FileDownload()
    resp = dl.open(url)
    
    #获取文件名
    filename = dl.filename if dl.filename else FilenameFromUrl(dl.realurl)
    if not filename:
        filename = 'NoName'
    
    if resp.status_code == 413:
        return 'too large', filename, ''
    elif resp.status_code not in (200,206):
        return 'download failed', filename, ''
    elif not resp.content:
        return 'not resuming', filename, ''
    else:
        return '', filename, resp.content
    
class FileDownload:
    def __init__(self, host=None, maxfetchcount=URLFETCH_MAX,
                timeout=URLFETCH_TIMEOUT, addreferer=False):
        self.cookie = Cookie.SimpleCookie()
        self.maxFetchCount = maxfetchcount
        self.host = host
        self.addReferer = addreferer
        self.timeout = timeout
        self.realurl = ''
        self.filelen = 0
        self.start = 0
        self.filename = ''
        
    def open(self, url):
        self.realurl = url
        
        class resp: #出现异常时response不是合法的对象，使用一个模拟的
            status_code=555
            content=None
            headers={}
            
        parts = []
        
        i = 0
        self.start = 0
        response = resp()
        HasTooLargeError = False
        RedirectCnt = 0
        
        #先判断是否支持断点续传，如果是小文件，可能已经正常下载了
        while i < self.maxFetchCount:
            try:
                response = urlfetch.fetch(url, payload=None, method=urlfetch.GET, 
                    headers=self._getHeaders(url, True), allow_truncated=False, 
                    follow_redirects=False, deadline=self.timeout, 
                    validate_certificate=False)
                urlnew = response.headers.get('Location')
                if urlnew:
                    url = urlnew if urlnew.startswith("http") else \
                        urlparse.urljoin(url, urlnew)
                    i = 0
                    RedirectCnt += 1
                    if RedirectCnt > 2:
                        break
                else:
                    disp = response.headers.get('Content-Disposition')
                    if disp:
                        s = re.search(r'(?i)filename\s*=\s*(.*)', disp)
                        if s:
                            self.filename = s.group(1).replace('\"', '')
                            if '/' in self.filename:
                                self.filename = self.filename.split('/')[-1]
                    break
            except urlfetch.ResponseTooLargeError as e:
                HasTooLargeError = True
                break
            #except Exception as e:
            #    i += 1
        
        self.realurl = url        
        content_range = response.headers.get('Content-Range')
        if response.status_code not in (200,206):
            return response
        elif not content_range:
            if HasTooLargeError:
                default_log.warn('server not support download file resuming at breakpoints.')
                response.content = ''
            
            return response
        
        #获取文件总长度
        self.filelen = 0
        try:
            self.filelen = int(content_range.split('/')[-1].strip())
        except:
            pass
        
        if self.filelen == 0:
            default_log.warn('server not support download file resuming at breakpoints.')
            response.content = ''
            return response
        elif self.filelen > 31457280: # 30MB
            default_log.warn('file is too large.')
            response.status_code = 413
            return response
            
        #保存第一部分(1k)
        parts.append(response.content)
        self.start = len(response.content)
        
        #正式下载
        RedirectCnt = 0
        while i < self.maxFetchCount:
            try:
                response = urlfetch.fetch(url, payload=None, method=urlfetch.GET, 
                    headers=self._getHeaders(url), allow_truncated=False, 
                    follow_redirects=False, deadline=self.timeout, 
                    validate_certificate=False)
            except OverQuotaError as e:
                default_log.warn('overquota(url:%r)' % url)
                time.sleep(5)
                i += 1
            except urlfetch.DeadlineExceededError as e:
                default_log.warn('timeout(deadline:%s, url:%r)' % (deadline, url))
                time.sleep(1)
                i += 1
            except urlfetch.DownloadError as e:
                default_log.warn('DownloadError(url:%r)' % url)
                time.sleep(1)
                i += 1
            except urlfetch.ResponseTooLargeError as e:
                default_log.warn('server not support download file resuming at breakpoints.')
                parts.clear()
                break
            except urlfetch.SSLCertificateError as e:
                #有部分网站不支持HTTPS访问，对于这些网站，尝试切换http
                if url.startswith(r'https://'):
                    url = url.replace(r'https://', r'http://')
                    i = 0
                    default_log.warn('server not support HTPPS, switch to http.')
                else:
                    break
            except Exception as e:
                break
            else:
                urlnew = response.headers.get('Location')
                if urlnew:
                    url = urlnew if urlnew.startswith("http") else \
                        urlparse.urljoin(url, urlnew)
                    i = 0
                    RedirectCnt += 1
                    if RedirectCnt > 2:
                        break
                elif len(response.content):
                    self.SaveCookies(response.header_msg.getheaders('Set-Cookie'))
                    parts.append(response.content)
                    default_log.warn('Dowlaod len : %s' % len(response.content)) #TODO
                    self.start += len(response.content)
                    if self.start >= self.filelen:
                        break
                    else:
                        i = 0 # 继续下载下一块
                else:
                    break
                
        self.realurl = url
        if parts:
            response.content = ''.join(parts)
        return response
        
    def _getHeaders(self, url=None, judge=False):
        headers = {
             'User-Agent':"Mozilla/5.0 (compatible; MSIE 9.0; Windows NT 6.1; Win64; x64; Trident/5.0)",
             'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                }
        cookie = '; '.join(["%s=%s" % (v.key, v.value) for v in self.cookie.values()])
        if cookie:
            headers['Cookie'] = cookie
        if judge: #用于判断服务器是否支持断点续传
            headers['Range'] = 'bytes=0-1023'
        elif self.start < self.filelen:
            if self.start+URLFETCH_MAXSIZE >= self.filelen:
                headers['Range'] = 'bytes=%d-%d' % (self.start, self.filelen-1)
            else:
                headers['Range'] = 'bytes=%d-%d' % (self.start, self.start+URLFETCH_MAXSIZE)
        if self.addReferer and (self.host or url):
            headers['Referer'] = self.host if self.host else url
        return headers
        
    def SaveCookies(self, cookies):
        if not cookies:
            return
        self.cookie.load(cookies[0])
        for cookie in cookies[1:]:
            obj = Cookie.SimpleCookie()
            obj.load(cookie)
            for v in obj.values():
                self.cookie[v.key] = v.value
            