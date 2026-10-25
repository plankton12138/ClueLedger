import { describe, expect, it } from "vitest";
import { DEFAULT_IDEA_POSITION, parseIdeaPosition } from "./ideasSurface";
import { floatingAnchor, floatingPoint } from "../components/ideas/useIdeasPosition";

describe("灵感位置边界", () => {
  it("损坏和非法存储回到默认位置，越界比例限制在可用范围", () => {
    expect(parseIdeaPosition("broken")).toEqual(DEFAULT_IDEA_POSITION);
    expect(parseIdeaPosition('{"x":"1","y":0.5}')).toEqual(DEFAULT_IDEA_POSITION);
    expect(parseIdeaPosition('{"x":8,"y":-1}')).toEqual({x:1,y:0});
  });
  it("窗口与面板大小变化后保持可达，拖动边缘不累计超出窗口的偏移", () => {
    const bounds={left:10,top:100,right:630,bottom:790};
    const size={width:344,height:400};
    expect(floatingPoint(bounds,size,{x:1,y:1})).toEqual({left:286,top:390});
    expect(floatingPoint(bounds,size,{x:0,y:0})).toEqual({left:10,top:100});
    const clamped=floatingPoint(bounds,size,floatingAnchor(bounds,size,{left:-900,top:4000}));
    expect(clamped).toEqual({left:10,top:390});
    const narrow={left:10,top:140,right:380,bottom:830};
    const moved=floatingAnchor(bounds,size,{left:160,top:180});
    const point=floatingPoint(narrow,size,moved);
    expect(point.left).toBeGreaterThanOrEqual(narrow.left);
    expect(point.left+size.width).toBeLessThanOrEqual(narrow.right);
    expect(point.top).toBeGreaterThanOrEqual(narrow.top);
    expect(point.top+size.height).toBeLessThanOrEqual(narrow.bottom);
  });
});
