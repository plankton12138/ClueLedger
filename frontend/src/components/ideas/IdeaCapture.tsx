/** 快速记录：只有一个正文输入框，提交成功才清空；输入法选词期间不提交。 */
import { forwardRef, useImperativeHandle, useRef } from "react";
import { useIdeasStore, useIdeaScope } from "../../state/ideas";
import { Button } from "../ui/Button";

export interface IdeaCaptureHandle {
  focus(): void;
}

const IdeaCapture = forwardRef<IdeaCaptureHandle>(function IdeaCapture(_, ref) {
  const scope = useIdeaScope();
  const busy = useIdeasStore(state => state.busy);
  const capture = useIdeasStore(state => state.capture);
  const setCaptureBody = useIdeasStore(state => state.setCaptureBody);
  const area = useRef<HTMLTextAreaElement>(null);
  const draft = scope.capture;
  const ready = draft.body.trim().length > 0;

  useImperativeHandle(ref, () => ({ focus: () => area.current?.focus() }), []);

  function submit() {
    void capture().then(result => {
      if (result.status !== "failed") area.current?.focus();
    });
  }

  return (
    <div className="idea-capture">
      <textarea
        ref={area}
        aria-label="记录灵感"
        className="ui-input idea-capture-input"
        placeholder="想到什么，先记下来"
        value={draft.body}
        onChange={event => setCaptureBody(event.target.value)}
        onKeyDown={event => {
          // 中文输入法选词期间的 Enter 只用于选词，不能当成提交。
          if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
          if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
            event.preventDefault();
            if (ready && !busy) submit();
          }
        }}
      />
      <div className="idea-capture-tools">
        <span className="ui-field-hint">{draft.body.length} / 20000</span>
        <Button
          variant="primary"
          size="compact"
          disabled={!ready}
          busy={busy}
          disabledReason="写一句想记下来的内容"
          onClick={submit}
        >
          记下
        </Button>
      </div>
    </div>
  );
});

export default IdeaCapture;
