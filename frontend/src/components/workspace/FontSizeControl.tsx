import { FONT_SCALE, useLayoutStore } from "../../state/layout";

/** 所有入口共用本机的字号偏好；百分比相对各区域原有字号。 */
export default function FontSizeControl() {
  const scale = useLayoutStore(state => state.fontScale);
  const setScale = useLayoutStore(state => state.setFontScale);
  const choices = [...new Set([80, 90, 100, 110, 120, 130, 140, 150, scale])].sort((a, b) => a - b);

  return (
    <div className="flex flex-none items-center gap-1 text-[11px]">
      <label className="flex items-center gap-1">
        字号
        <select aria-label="调整字体大小" className="rounded border border-[#cfc7ae] bg-[#fbf8ef] px-1 py-1 text-[#4b4737]"
          value={scale} onChange={event => setScale(Number(event.target.value))}>
          {choices.map(value => <option key={value} value={value}>{value}%</option>)}
        </select>
      </label>
      <button type="button" className="btn btn-gray px-2 py-1" aria-label="恢复默认字号"
        disabled={scale === FONT_SCALE.default} onClick={() => setScale(FONT_SCALE.default)}>
        重置
      </button>
    </div>
  );
}
