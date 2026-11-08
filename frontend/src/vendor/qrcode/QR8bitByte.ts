// @ts-nocheck
// Vendor 自 qrcode-terminal 0.12.0（vendor/QRCode），上游为 Kazuhiko Arase 的
// qrcode-generator（MIT License）。只做了 CommonJS 到 ESM 的机械转换，逻辑未改动。
import QRMode from './QRMode';

function QR8bitByte(data) {
	this.mode = QRMode.MODE_8BIT_BYTE;
	this.data = data;
}

QR8bitByte.prototype = {

	getLength : function() {
		return this.data.length;
	},
	
	write : function(buffer) {
		for (var i = 0; i < this.data.length; i++) {
			// not JIS ...
			buffer.put(this.data.charCodeAt(i), 8);
		}
	}
};

export default QR8bitByte;
