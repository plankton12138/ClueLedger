// @ts-nocheck
import QRCode from "./QRCode";

/** 把文本编码成二维码矩阵；level 固定用 L，链接越短码越疏，屏幕更好扫。 */
export function qrModules(text: string): boolean[][] {
  const qr = new QRCode(0, 1);
  qr.addData(text);
  qr.make();
  const count = qr.getModuleCount();
  const rows: boolean[][] = [];
  for (let row = 0; row < count; row += 1) {
    const cells: boolean[] = [];
    for (let col = 0; col < count; col += 1) cells.push(qr.isDark(row, col));
    rows.push(cells);
  }
  return rows;
}
