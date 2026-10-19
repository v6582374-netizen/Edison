import DOMPurify from 'dompurify'
import { EPUB } from 'foliate-js/epub.js'
import { configure,ZipReader,BlobReader,TextWriter,BlobWriter } from 'foliate-js/vendor/zip.js'

export const CONTENT_RULE = 'reader-safe-epub-v1'
const CSP = "default-src 'none'; script-src 'none'; style-src 'unsafe-inline' blob:; img-src blob: data:; font-src blob: data:; media-src 'none'; frame-src 'none'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
const markup = new Set(['application/xhtml+xml','text/html','image/svg+xml'])
const forbidden = ['script','iframe','object','embed','form','input','button','textarea','select','meta','base','audio','video','foreignObject','animate','set','animateMotion','animateTransform']

function sanitize(text,type) {
    const svg=type==='image/svg+xml'
    const clean=DOMPurify.sanitize(text,{
        WHOLE_DOCUMENT:!svg,RETURN_DOM:true,FORBID_TAGS:forbidden,
        // Keep book fragment IDs (including ordinary IDs such as 'target').
        // Named document properties require name-bearing elements; active element
        // types are forbidden and names are removed. The book runs no scripts.
        SANITIZE_DOM:false,FORBID_ATTR:['name'],
        ADD_TAGS:svg?[]:['link'],ADD_ATTR:['epub:type','xml:lang'],
        ...(svg?{USE_PROFILES:{svg:true,svgFilters:true}}:{}),
    })
    if(svg) {
        const root=clean.localName==='svg'?clean:clean.querySelector('svg')
        if(!root)throw new Error('无效的 SVG 资源')
        return new XMLSerializer().serializeToString(root)
    }
    clean.setAttributeNS('http://www.w3.org/2000/xmlns/','xmlns:epub','http://www.idpf.org/2007/ops')
    const doc=clean.ownerDocument
    let head=clean.querySelector('head')
    if(!head){head=doc.createElementNS('http://www.w3.org/1999/xhtml','head');clean.prepend(head)}
    const policy=doc.createElementNS('http://www.w3.org/1999/xhtml','meta')
    policy.setAttribute('http-equiv','Content-Security-Policy');policy.setAttribute('content',CSP)
    head.prepend(policy)
    return new XMLSerializer().serializeToString(clean)
}

/** No fetch inside the book loader: every resource is a bounded archive member.
 * Search/createDocument and rendering receive the SAME sanitized XHTML bytes.
 * Original EPUB remains untouched; locator identity includes CONTENT_RULE.
 */
export async function makeSafeEPUB(file) {
    if(typeof file==='string') {
        const response=await fetch(file)
        if(!response.ok)throw new Error('无法读取 EPUB')
        file=new File([await response.blob()],'book.epub')
    }
    if(!file.size||file.size>64*1024*1024)throw new Error('EPUB 文件大小须在 0 至 64 MB 之间')
    configure({useWebWorkers:false})
    const reader=new ZipReader(new BlobReader(file))
    try {
        const entries=await reader.getEntries()
        if(entries.length>10000)throw new Error('EPUB 资源数量过多')
        let total=0
        const map=new Map()
        for(const entry of entries) {
            const path=entry.filename
            if(path.startsWith('/')||path.includes('\\')||path.includes('\0')||path.split('/').some(x=>x==='..')||map.has(path))
                throw new Error('EPUB 包含不安全或重复的资源路径')
            total+=entry.uncompressedSize
            if(entry.uncompressedSize>64*1024*1024||total>256*1024*1024||entry.encrypted)
                throw new Error('EPUB 加密或解压体积超过限制')
            map.set(path,entry)
        }
        const types=new Map(),cache=new Map()
        const raw=async name=>map.has(name)?map.get(name).getData(new TextWriter()):null
        const loadText=async name=>{
            if(!markup.has(types.get(name)))return raw(name)
            if(!cache.has(name))cache.set(name,raw(name).then(text=>text==null?null:sanitize(text,types.get(name))))
            return cache.get(name)
        }
        const book=await new EPUB({
            loadText,
            loadBlob:async name=>map.has(name)?map.get(name).getData(new BlobWriter()):null,
            getSize:name=>map.get(name)?.uncompressedSize??0,
        }).init()
        for(const item of book.resources.manifest)types.set(item.href,item.mediaType)
        if(book.rendition?.layout==='pre-paginated')throw new Error('暂不支持固定版式 EPUB；请使用可重排版本')
        if(book.sections.some(section=>!['application/xhtml+xml','text/html'].includes(types.get(section.id))))
            throw new Error('暂不支持以图片或 SVG 作为独立章节的 EPUB')
        const destroy=book.destroy.bind(book)
        let destroyed=false
        book.destroy=()=>{if(destroyed)return;destroyed=true;destroy();cache.clear();void reader.close()}
        book.contentRule=CONTENT_RULE
        return book
    } catch(error){await reader.close();throw error}
}
