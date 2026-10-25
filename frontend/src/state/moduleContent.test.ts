import { describe,expect,it } from "vitest";
import { contentName,moduleOutline,type ModuleDocument } from "./moduleContent";

describe("模块正文与内容目录",()=>{
  it("名称可从正文首行取得，空内容也可直接创建",()=>{
    expect(contentName("","## 雾港疑案\n\n角色与线索")).toBe("雾港疑案");
    expect(contentName("  主角团  ","正文")).toBe("主角团");
    expect(contentName("","")).toBe("未命名模块");
    expect(contentName("","线"+"𠮷".repeat(80))).toBe("线"+"𠮷".repeat(79));
  });
  it("目录只取真正的小标题，旧条目标题仍能定位",()=>{
    const document:ModuleDocument={detail:{library_id:"lib",module_id:"module",revision_id:"revision",kind:"reference",name:"雾港疑案",entries:[{entry_id:"content",kind:"reference",title:"雾港疑案",aliases:[],fields:{},chars:100,digest:"a"}]},bodies:{content:"## 角色小传\n\n```text\n# 这不是小标题\n```\n\n### 真相"}};
    expect(moduleOutline(document)).toEqual([{id:"module-heading-content-1",title:"角色小传",level:2},{id:"module-heading-content-7",title:"真相",level:3}]);
    document.detail.entries[0].title="角色";
    expect(moduleOutline(document)[0]).toMatchObject({id:"module-entry-content",title:"角色"});
  });
});
