import type { ReactNode } from "react";
import * as Primitive from "@radix-ui/react-tabs";
export function Tabs({ label, value, onValueChange, items }: { label: string; value: string; onValueChange: (value: string) => void; items: { value: string; label: string; content: ReactNode; disabled?: boolean }[] }) {
  return <Primitive.Root value={value} onValueChange={onValueChange} className="ui-tabs">
    <Primitive.List aria-label={label} className="ui-tab-list">{items.map(item => <Primitive.Trigger className="ui-tab" key={item.value} value={item.value} disabled={item.disabled}>{item.label}</Primitive.Trigger>)}</Primitive.List>
    {items.map(item => <Primitive.Content className="ui-tab-content" key={item.value} value={item.value}>{item.content}</Primitive.Content>)}
  </Primitive.Root>;
}
