/** Shared image residency budget across raw/QC maps. Active frames are pinned. */
type Loaded = { url: string; bytes: number; dispose: () => void }
type Entry = { key: string; refs: number; touched: number; loaded?: Loaded; controller: AbortController; promise: Promise<Loaded> }
export class RadarImagePool {
  private entries = new Map<string, Entry>()
  private active = 0
  private queue: (()=>void)[] = []
  private bytes = 0
  constructor(private loader: (url:string,signal:AbortSignal)=>Promise<Loaded>, readonly budget=128*1024*1024, readonly concurrency=4) {}
  stats() { return {bytes:this.bytes,active:this.active,queued:this.queue.length,entries:this.entries.size} }
  acquire(key:string) {
    let entry=this.entries.get(key)
    if(!entry){
      const controller=new AbortController()
      entry={key,refs:0,touched:Date.now(),controller,promise:Promise.resolve(undefined as unknown as Loaded)}
      const current=entry
      current.promise=new Promise<Loaded>((resolve,reject)=>{
        this.queue.push(()=>{
          if(controller.signal.aborted){reject(new DOMException('Cancelled','AbortError'));this.pump();return}
          this.active++
          void this.loader(key,controller.signal).then(value=>{
            if(controller.signal.aborted){value.dispose();throw new DOMException('Cancelled','AbortError')}
            this.evict(value.bytes)
            if(this.bytes+value.bytes>this.budget){value.dispose();throw new Error('地图图层超过 128 MiB 内存预算，请减少同时显示的站点')}
            current.loaded=value;this.bytes+=value.bytes;resolve(value)
          }).catch(error=>{if(this.entries.get(key)===current)this.entries.delete(key);reject(error)})
            .finally(()=>{this.active--;this.pump()})
        })
      })
      this.entries.set(key,current)
    }
    entry.refs++;entry.touched=Date.now();this.pump()
    const selected=entry;let released=false
    return {promise:selected.promise,release:()=>{
      if(released)return;released=true;selected.refs--;selected.touched=Date.now()
      if(selected.refs===0&&!selected.loaded){selected.controller.abort();if(this.entries.get(key)===selected)this.entries.delete(key)}
      this.evict(0)
    }}
  }
  clearIdle(){ for(const entry of this.entries.values())if(!entry.refs)this.remove(entry) }
  private remove(entry:Entry){entry.controller.abort();if(entry.loaded){this.bytes-=entry.loaded.bytes;entry.loaded.dispose()}this.entries.delete(entry.key)}
  private evict(incoming:number){const idle=[...this.entries.values()].filter(e=>!e.refs&&e.loaded).sort((a,b)=>a.touched-b.touched);for(const entry of idle){if(this.bytes+incoming<=this.budget)break;this.remove(entry)}}
  private pump(){while(this.active<this.concurrency&&this.queue.length)this.queue.shift()!()}
}
export const radarImagePool=new RadarImagePool(async(url,signal)=>{
  const response=await fetch(url,{signal});if(!response.ok)throw new Error(`图件读取失败（${response.status}）`)
  const blob=await response.blob();if(blob.type!=='image/png')throw new Error('图件格式错误')
  const objectURL=URL.createObjectURL(blob);const image=new Image();image.src=objectURL
  try{await image.decode();return {url:objectURL,bytes:image.naturalWidth*image.naturalHeight*4,dispose:()=>URL.revokeObjectURL(objectURL)}}catch(error){URL.revokeObjectURL(objectURL);throw error}
})
