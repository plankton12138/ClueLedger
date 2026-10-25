import { useLayoutEffect, useRef, useState } from "react";
import { useModuleLibraryStore } from "../../state/moduleLibrary";
import { usePagePositions } from "../../state/pagePositions";
import type { ModuleInfo } from "../../api/moduleLibrary";
import { Button } from "../ui/Button";
import { EmptyState, StatusMessage } from "../ui/StatusMessage";

export default function LibraryBrowser({ onSelect,onAdd }: { onSelect: (id:string)=>void; onAdd: (item:ModuleInfo)=>void }) {
  const store = useModuleLibraryStore();
  const [search,setSearch] = useState(store.query);
  const scroll = useRef<HTMLDivElement>(null);
  useLayoutEffect(()=>{ setSearch(store.query); },[store.query]);
  useLayoutEffect(()=>{ if(scroll.current)scroll.current.scrollTop=usePagePositions.getState().offsets["library-list"]??0; },[store.modules]);
  return <section className="library-browser module" aria-label="浏览模块库">
    <form className="library-browser-tools" onSubmit={event=>{event.preventDefault();store.setQuery(search);}}>
      <input className="ui-input" aria-label="搜索模块" placeholder="搜索模块" value={search} onChange={event=>setSearch(event.target.value)} />
      <Button size="compact" type="submit">查找</Button>
    </form>
    <div className="library-list" ref={scroll} onScroll={event=>{if(event.currentTarget.clientHeight&&store.modules.length)usePagePositions.getState().offset("library-list",event.currentTarget.scrollTop);}}>
      {store.loading ? <StatusMessage>正在读取模块库…</StatusMessage> : <>
        {store.modules.length ? <ul className="module-library-shelf">{store.modules.map(item=>{
          const added=store.assembly?.bindings.some(binding=>binding.module.module_id===item.module_id);
          return <li key={item.module_id} className="module-library-card"><button type="button" className="module-library-card-name" onClick={()=>onSelect(item.module_id)}>{item.name}</button>
            {item.preview && <p>{item.preview}</p>}
            <div className="module-library-card-actions">{item.deleted ? <span>已删除</span> : <Button size="compact" disabled={Boolean(added)||!store.workspaceId||store.busy||!item.head} disabledReason={added ? "已添加到当前作品" : !store.workspaceId ? "先打开一个作品" : undefined} onClick={()=>onAdd(item)}>{added?"已添加":"＋ 添加"}</Button>}</div>
          </li>;
        })}</ul> : <EmptyState>还没有匹配的模块。</EmptyState>}
        {store.cursor&&<Button variant="quiet" busy={store.loadingMore} onClick={()=>void store.loadMore()}>加载更多</Button>}
      </>}
    </div>
  </section>;
}
