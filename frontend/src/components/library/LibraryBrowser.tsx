import { useLayoutEffect, useRef, useState } from "react";
import { useModuleLibraryStore } from "../../state/moduleLibrary";
import { usePagePositions } from "../../state/pagePositions";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { ListItem } from "../ui/ListItem";
import { EmptyState, StatusMessage } from "../ui/StatusMessage";
import { KIND_LABEL, KIND_OPTIONS } from "./labels";

export default function LibraryBrowser({ onSelect, onCreate }: { onSelect: (id: string) => void; onCreate: () => void }) {
  const store = useModuleLibraryStore();
  const [search, setSearch] = useState(store.query);
  const scroll = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => { setSearch(store.query); }, [store.query]);
  useLayoutEffect(() => { if (scroll.current) scroll.current.scrollTop = usePagePositions.getState().offsets["library-list"] ?? 0; }, [store.modules]);
  return <aside className="library-browser module" aria-label="浏览模块库">
    <div className="library-browser-tools"><Field label="搜索模块"><input aria-label="搜索模块" value={search} onChange={event => setSearch(event.target.value)}
      onKeyDown={event => { if (event.key === "Enter" && !event.nativeEvent.isComposing) store.setQuery(search); }} /></Field>
      <Button size="compact" onClick={() => store.setQuery(search)}>查找</Button>
    </div>
    <div className="library-filters" role="group" aria-label="模块类型">
      <Button variant={store.kind === "all" ? "primary" : "quiet"} size="compact" onClick={() => store.setKind("all")}>全部</Button>
      {KIND_OPTIONS.map(kind => <Button key={kind} size="compact" variant={store.kind === kind ? "primary" : "quiet"} onClick={() => store.setKind(kind)}>{KIND_LABEL[kind]}</Button>)}
    </div>
    <label className="library-deleted"><input type="checkbox" checked={store.includeDeleted} onChange={event => store.setIncludeDeleted(event.target.checked)} />显示已删除</label>
    <div className="library-list" ref={scroll} onScroll={event => { if (event.currentTarget.clientHeight && store.modules.length) usePagePositions.getState().offset("library-list", event.currentTarget.scrollTop); }}>
      {store.loading ? <StatusMessage>正在读取模块库…</StatusMessage> : <>
        {store.modules.length ? <ul>{store.modules.map(item => <li key={item.module_id}><ListItem selected={item.module_id === store.selectedModuleId}
          summary={`${KIND_LABEL[item.kind]} · ${item.revision_count ?? 1} 个版本${item.deleted ? " · 已删除" : ""}`} onClick={() => onSelect(item.module_id)}>{item.name}</ListItem></li>)}</ul>
          : <EmptyState>没有匹配的模块。</EmptyState>}
        {store.cursor && <Button variant="quiet" busy={store.loadingMore} onClick={() => void store.loadMore()}>加载更多（共 {store.total} 项）</Button>}
      </>}
    </div>
    <footer><Button variant="primary" onClick={onCreate}>新建模块</Button></footer>
  </aside>;
}
