# Ghi chú theo nền tảng (Windows / Ubuntu / macOS)

> 🌐 Ngôn ngữ: [English](../platforms.md) · **Tiếng Việt**

Những ghi chú này chỉ nói về đường in **WebUSB qua trình duyệt** — bind một máy in cắm vào *cùng máy
tính với trình duyệt*. Nếu bạn dùng **local bridge** (máy in cắm vào máy chủ) hoặc một **agent node-in
[agent](../../agent/README-vi.md)** (máy in cắm vào chiếc Pi của chính bạn), thì chẳng có điều nào ở
đây áp dụng cả: máy in được điều khiển ở phía máy chủ và mọi máy in nhiệt USB đều chạy được. Trên
macOS/Windows, một agent chạy trên một máy Linux nhỏ / một chiếc Pi là câu trả lời đỡ đau đầu nhất.

## Hai quy tắc bất di bất dịch của WebUSB

1. **Secure context.** Trình duyệt chỉ phơi ra `navigator.usb` trên **HTTPS** hoặc **`localhost`**.
   Truy cập `http://<IP-LAN-hoặc-tailscale>:8000` từ một máy khác → WebUSB bị chặn. Cách khắc phục:
   `tailscale serve` (HTTPS thật), một reverse proxy có TLS, hoặc cờ Chrome
   `chrome://flags/#unsafely-treat-insecure-origin-as-secure` để thử nghiệm.
2. **Chỉ Chromium.** WebUSB chạy trong Chrome / Edge / Brave / Opera. **Safari và Firefox hoàn toàn
   không hỗ trợ WebUSB.**

Yêu cầu còn lại là interface USB của máy in phải **chiếm được** — tức là chưa bị một driver của OS
giữ. Đây chính là chỗ ba nền tảng khác nhau.

---

## 🐧 Ubuntu / Linux

**Hỗ trợ WebUSB tốt nhất** — Linux cho phép Chrome tách driver nhân ra.

- Khi bạn cắm một máy in ESC/POS, nhân bind `usblp` và tạo `/dev/usb/lp0`. Điều đó hoàn hảo cho
  **local bridge**, nhưng lại *chặn* WebUSB chiếm cùng interface đó.
- Để dùng **WebUSB** trên Linux, hãy giải phóng interface trước:
  ```bash
  sudo modprobe -r usblp          # gỡ driver máy in của nhân
  ```
  …rồi thiết bị sẽ hiện trong bộ chọn của Chrome và chiếm được. **Lưu ý:** cách này tắt local bridge
  (không còn `/dev/usb/lp0` nữa), nên hãy chọn một trong hai đường.
- Chrome cũng cần quyền mở node USB thô. Một udev rule cấp quyền đó:
  ```
  # /etc/udev/rules.d/99-webusb.rules
  SUBSYSTEM=="usb", ATTR{idVendor}=="28e9", MODE="0666"
  ```
  (thay `28e9` bằng vendor id của máy in bạn lấy từ `lsusb`), rồi
  `sudo udevadm control --reload-rules && sudo udevadm trigger`.
- **Trên máy chủ (vd Raspberry Pi)** bạn thường muốn điều ngược lại: giữ `usblp` được nạp và dùng
  local bridge. `deploy/install.sh` sẽ thiết lập việc đó (udev rule + nhóm `lp`).

## 🍎 macOS

**WebUSB tới một máy in về cơ bản là không chạy** — và đây không phải lỗi của FaxxMe.

- macOS tự động chiếm lấy các **máy in USB tuân chuẩn class** (như PT-280) vào hệ thống in của chính
  nó. Chrome không tách được driver đó trên macOS, nên máy in **không xuất hiện** trong bộ chọn WebUSB
  → bạn nhận thông báo *"No compatible devices found."*
- Không có thao tác "gỡ driver" thân thiện nào tương đương `modprobe -r` của Linux.
- Safari/Firefox thì đằng nào cũng không hỗ trợ WebUSB.

**Nên làm gì trên máy Mac:**

- **Khuyến nghị:** đừng dùng máy in của Mac qua trình duyệt. Hãy gắn máy in vào một Raspberry Pi /
  máy Linux và dùng **local bridge** — máy Mac chỉ việc fax tới callsign đó.
- Hoặc dùng đường dự phòng **in qua trình duyệt** (hộp thoại in của OS) cho các bản in thủ công, khi
  bấm nút.
- Các thiết bị enumerate dưới dạng interface **vendor-specific** (vd một số gadget ESP32) *có* hiện
  trong WebUSB trên macOS — nhưng máy in nhiệt tiêu chuẩn thì không.

## 🪟 Windows

Tương tự macOS: Windows bind driver **usbprint** của nó vào các thiết bị class máy in, nên chúng mặc
định sẽ không hiện trong bộ chọn WebUSB của Chrome.

- Để ép một thiết bị sang driver **WinUSB** generic (để WebUSB chiếm được), hãy dùng
  **[Zadig](https://zadig.akeo.ie/)** gán lại driver cho máy in đó. Việc này làm nó truy cập được qua
  WebUSB nhưng **loại nó khỏi hệ thống in bình thường của Windows** — một sự đánh đổi có chủ đích, và
  hơi lằng nhằng. Chỉ đáng làm với một máy in FaxxMe chuyên dụng.
- Chỉ Chrome/Edge (Firefox không có WebUSB).
- **Đường dễ nhất, như ở mọi nơi:** chạy máy chủ trên một máy Linux có gắn máy in và dùng **local
  bridge**; các client Windows chỉ việc fax tới callsign.

---

## Tóm gọn

| bạn đang dùng… | muốn dùng máy in trên chính máy này qua trình duyệt? | hãy làm |
|------------|-----------------------------------------------|---------|
| Ubuntu/Linux | có | `sudo modprobe -r usblp` + udev rule + HTTPS, Chrome |
| macOS | gần như không thể | chạy **[agent](../../agent/README-vi.md)** trên một chiếc Pi, hoặc dùng in-qua-trình-duyệt |
| Windows | được nhưng lằng nhằng | Zadig → WinUSB + HTTPS, Chrome; nếu không thì agent trên một chiếc Pi |
| **bất kỳ** | **chỉ muốn nó chạy được** | **gắn máy in vào một chiếc Pi và chạy agent (hoặc local bridge trên host)** |
