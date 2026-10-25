from pathlib import Path
from zipfile import ZipFile, ZIP_STORED, ZIP_DEFLATED
import base64, struct, zlib
def chunk(kind,data):
    return struct.pack(">I",len(data))+kind+data+struct.pack(">I",zlib.crc32(kind+data)&0xffffffff)
png=b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",8,8,8,2,0,0,0))+chunk(b"IDAT",zlib.compress((b"\0"+bytes([40,120,160])*8)*8))+chunk(b"IEND",b"")
ROOT=Path(__file__).parent
container='<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
for version in (2,3):
    nav='<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>目录</title></head><body><nav epub:type="toc"><ol><li><a href="text/ch1.xhtml#target">第一章</a><ol><li><a href="text/ch2.xhtml#note">注释</a></li></ol></li></ol></nav></body></html>'
    ncx='<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="urn:spike"/></head><docTitle><text>验证书</text></docTitle><navMap><navPoint id="p1" playOrder="1"><navLabel><text>第一章</text></navLabel><content src="text/ch1.xhtml#target"/></navPoint><navPoint id="p2" playOrder="2"><navLabel><text>注释</text></navLabel><content src="text/ch2.xhtml#note"/></navPoint></navMap></ncx>'
    manifest='<item id="ch1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/><item id="ch2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/><item id="img" href="images/pixel.png" media-type="image/png"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    if version==3:manifest+='<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    opf=f'<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid" version="{version}.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">urn:spike:{version}</dc:identifier><dc:title>EPUB {version} 定位验证</dc:title><dc:language>zh-CN</dc:language>'+('<meta property="dcterms:modified">2026-10-14T00:00:00Z</meta>' if version==3 else '')+f'</metadata><manifest>{manifest}</manifest><spine toc="ncx"><itemref idref="ch1"/><itemref idref="ch2"/></spine></package>'
    paragraphs=''.join(f'<p>第{i}段。每次重排只改变页面，不应改变文字的身份。阅读记录需要准确。</p>' for i in range(40))
    body='<h1>定位验证</h1><p id="first">相同句子不能混淆。</p>'+paragraphs+'<p id="target">前缀甲：中文<strong>跨标签</strong> English 😀 e\u0301 终点。后缀乙。</p><p id="second">相同句子不能混淆。</p><p><a id="footnote" href="ch2.xhtml#note">脚注一</a></p><img id="image" src="../images/pixel.png" alt="测试图片" width="80" height="80"/>'
    body+='<script>parent.__epubExecuted=true</script><img src="missing" onerror="parent.__epubExecuted=true"/>'
    def xhtml(body):return '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>验证</title></head><body>'+body+'</body></html>'
    with ZipFile(ROOT/f'epub{version}.epub','w') as z:
        z.writestr('mimetype','application/epub+zip',compress_type=ZIP_STORED)
        for path,data in {'META-INF/container.xml':container,'OPS/book.opf':opf,'OPS/nav.xhtml':nav,'OPS/toc.ncx':ncx,'OPS/text/ch1.xhtml':xhtml(body),'OPS/text/ch2.xhtml':xhtml('<h1 id="note">脚注内容</h1><p>脚注应准确跳到这里。</p><a href="ch1.xhtml#target">返回正文</a>'),'OPS/images/pixel.png':png}.items():z.writestr(path,data,compress_type=ZIP_DEFLATED)
print('Generated EPUB 2 NCX and EPUB 3 nav fixtures (synthetic, not conformance-certified).')

# Many literal matches and a hidden passage exercise bounded search and honest
# navigation failure without using a private book or a network resource.
with ZipFile(ROOT/'epub3.epub') as source, ZipFile(ROOT/'search-many.epub','w') as target:
    for entry in source.infolist():
        data=source.read(entry.filename)
        if entry.filename=='OPS/text/ch1.xhtml':
            data=xhtml(''.join(f'<p>第{i}段：查找边界。</p>' for i in range(350))+'<p style="display:none">隐藏测试文字</p>').encode()
        target.writestr(entry,data)
