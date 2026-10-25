import { Button } from "../ui/Button";
import { FONT_SCALE, useLayoutStore } from "../../state/layout";

/** 所有入口共用本机的字号偏好；百分比相对各区域原有字号。 */
export default function FontSizeControl() {
  const scale = useLayoutStore(state => state.fontScale);
  const setScale = useLayoutStore(state => state.setFontScale);
  const choices = [...new Set([80, 90, 100, 110, 120, 130, 140, 150, scale])].sort((a, b) => a - b);

  return (
    <div className="flex flex-none flex-wrap items-center gap-2 text-sm">
      <label className="flex items-center gap-1">
        字号
        <select aria-label="调整字体大小" className="ui-input"
          value={scale} onChange={event => setScale(Number(event.target.value))}>
          {choices.map(value => <option key={value} value={value}>{value}%</option>)}
        </select>
      </label>
      <Button size="compact" aria-label="恢复默认字号" disabledReason="当前已是默认字号"
        disabled={scale === FONT_SCALE.default} onClick={() => setScale(FONT_SCALE.default)}>
        重置
      </Button>
    </div>
  );
}
