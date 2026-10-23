import tailwindcss from "tailwindcss";
import autoprefixer from "autoprefixer";

// 同时覆盖组件样式、Tailwind 字号及任意 px 字号，避免遗漏日志中的小字。
// em、百分比和无单位行高随父字号变化，不能再次乘比例；尺寸与间距保持原样。
const fontScale = {
  postcssPlugin: "clueledger-font-scale",
  OnceExit(root) {
    root.walkDecls(/^(font-size|line-height)$/, declaration => {
      if (/^(html|:root)$/.test(declaration.parent.selector?.trim() ?? "")) return;
      if (/^\d*\.?\d+(px|rem)$/.test(declaration.value)) {
        declaration.value = `calc(${declaration.value} * var(--font-scale, 1))`;
      }
    });
  },
};

export default { plugins: [tailwindcss(), autoprefixer(), fontScale] };
