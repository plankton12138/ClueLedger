import { describe, expect, it } from "vitest";
import { DEFAULT_IDEA_POSITION, parseIdeaPosition } from "./ideasSurface";
import { floatingAnchor, floatingPoint } from "../components/ideas/useIdeasPosition";

describe("灵感位置边界", () => {
  it("损坏和非法存储回到默认位置，越界比例限制在可用范围", () => {
    expect(parseIdeaPosition("broken")).toEqual(DEFAULT_IDEA_POSITION);
    expect(parseIdeaPosition('{"x":"1","y":0.5}')).toEqual(DEFAULT_IDEA_POSITION);
    expect(parseIdeaPosition('{"x":8,"y":-1}')).toEqual({x:2,y:0});
  });
  it("完整可见的范围保持原样，拖出窗口时只保留标题栏可抓取", () => {
    const bounds={left:10,top:100,right:630,bottom:790};
    const size={width:344,height:400};
    expect(floatingPoint(bounds,size,{x:1,y:1})).toEqual({left:286,top:390});
    expect(floatingPoint(bounds,size,{x:0,y:0})).toEqual({left:10,top:100});
    expect(floatingPoint(bounds,size,{x:2,y:0.5}).left).toBe(630-48);
    expect(floatingPoint(bounds,size,{x:0.5,y:2}).top).toBe(790-48);
  });
  it("拖动时位置与指针一致，窗口缩小后仍保留可抓取部分", () => {
    const bounds={left:10,top:100,right:630,bottom:790};
    const size={width:344,height:400};
    const raw={left:520,top:700};
    const roundtrip=floatingPoint(bounds,size,floatingAnchor(bounds,size,raw));
    expect(roundtrip.left).toBeCloseTo(raw.left);
    expect(roundtrip.top).toBeCloseTo(raw.top);
    const narrow={left:10,top:140,right:380,bottom:830};
    const point=floatingPoint(narrow,size,floatingAnchor(bounds,size,{left:160,top:700}));
    expect(point.left).toBeGreaterThanOrEqual(narrow.left);
    expect(narrow.right-point.left).toBeGreaterThanOrEqual(48);
    expect(point.top).toBeGreaterThanOrEqual(narrow.top);
    expect(narrow.bottom-point.top).toBeGreaterThanOrEqual(48);
  });
});
