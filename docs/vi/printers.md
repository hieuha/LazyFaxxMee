# Tương thích máy in

> 🌐 Ngôn ngữ: [English](../printers.md) · **Tiếng Việt**

FaxxMe nói chuyện bằng **ESC/POS thô** — ngôn ngữ lệnh mà gần như mọi máy in nhiệt USB/Bluetooth giá
rẻ đều hiểu. Có ba cách để các byte đến được máy in; mỗi cách có yêu cầu khác nhau.

## Các đường in

| đường | chạy ở đâu | cần gì | hợp nhất với |
|------|---------------|-------|----------|
| **Local bridge** | máy chủ (host) | máy in cắm vào host; một `/dev/usb/lp*` ghi được (Linux) | một Raspberry Pi / máy luôn-bật chạy cả app lẫn máy in |
| **Node in (agent)** | chiếc Pi của chính người nhận | [agent](../../agent/README-vi.md) + một device token; một `/dev/usb/lp*` ghi được | mỗi người dùng có máy in riêng, ở bất cứ đâu có internet |
| **WebUSB qua trình duyệt** | trình duyệt của người nhận | Chromium, HTTPS/localhost, một interface USB *chiếm được* | một máy in cắm vào cùng máy tính với trình duyệt |
| **In qua trình duyệt** (dự phòng) | trình duyệt của người nhận | bất kỳ máy in nào OS đã cài + driver | in thủ công một lần (cần bấm; không tự in được) |

**Local bridge** và **agent node-in** là hai đường đáng tin cậy nhất (ESC/POS dựng ở máy chủ →
`/dev/usb/lp*`, không dính các trò lắt léo của trình duyệt). WebUSB thì đỏng đảnh và phụ thuộc OS —
xem [platforms.md](platforms.md).

## ⚡ Nguồn & USB — nguyên nhân số 1 gây in cụt và loop in lại

**Đừng cấp nguồn cho máy in nhiệt từ cổng USB của Raspberry Pi.** Đầu in nhiệt hút **dòng đỉnh
lớn** khi in, và máy in loại "mobile"/có pin còn **hút thêm dòng sạc** qua cùng sợi cáp USB. Cổng
USB của Pi không cấp nổi — **mạch bảo vệ over-current của Pi cắt điện cổng**, máy in **rớt khỏi bus
USB giữa lúc in**, rồi ~1 giây sau nối lại. Lặp lại thì biểu hiện là:

- tin dài hoặc ảnh chỉ in được **một đoạn** rồi dừng (kết nối chết giữa chừng); và/hoặc
- **cùng một fax in đi in lại** trong khi UI vẫn báo **`queued`** (máy in rớt ngay sau khi in xong
  nên tín hiệu "đã in" không về được server → fax bị xếp hàng lại → in tiếp).

**Cách xác nhận là do nguồn** (chạy trên host):

```bash
dmesg | grep -iE "over-current|usblp0|disconnect" | tail    # over-current + "usblp0: removed" lặp = đúng lỗi này
dmesg | grep -c over-current                                # số cứ tăng = đang diễn ra
vcgencmd get_throttled                                       # 0x0 = nguồn lõi Pi OK; khác 0 = sụt áp thêm
```

Nếu thấy `over-current change` và `usblp0: removed` lặp liên tục (**kể cả lúc rảnh**) thì là nguồn —
không thiết lập phần mềm nào chữa được.

**Cách sửa (chọn một):**

1. **USB hub có nguồn riêng** (tốt nhất) — cắm máy in vào hub **có adapter điện riêng**, rồi hub nối
   vào Pi. Máy in lấy dòng từ adapter của hub, không rút từ Pi.
2. **Cấp nguồn riêng cho máy in** — máy in mobile/pin thì **sạc bằng sạc tường**, chỉ dùng USB của Pi
   cho *data*; máy in có giắc nguồn thì dùng giắc đó.
3. Dùng **nguồn Pi đủ mạnh** (USB-C 5V/3A+ chính hãng cho Pi 4/5) và **cáp USB tốt, ngắn** — nguồn
   yếu hoặc cáp mỏng/dài làm nặng thêm. Vài dòng Pi có thể đặt `max_usb_current=1` trong
   `/boot/firmware/config.txt` để nới giới hạn cổng, nhưng powered hub mới là cách chuẩn.

**Lưới an toàn phần mềm:** nếu ghi cứ lỗi, FaxxMe/agent sẽ **bỏ cuộc sau `FAXXME_BRIDGE_MAX_ATTEMPTS`
lần** (mặc định `3`) và đánh dấu fax đã giao, để máy in chập chờn **không in lại vô hạn**. Cái này
chỉ **giới hạn thiệt hại**, không thay cho việc sửa nguồn.

## Cái gì chạy tốt

Bất kỳ **máy in nhiệt USB ESC/POS** nào mà OS phơi ra dưới dạng một máy in dòng (line printer) thô.
Các dòng đã được kiểm chứng:

- **58 mm mini/cầm tay** — GOOJPRT PT-210 / PT-280, "micro-printer" của GDMicroelectronics (USB id
  `28e9:0289`, chính là thiết bị dự án này được phát triển trên đó), MUNBYN, Xprinter, Rongta, Zjiang.
  Đây là các máy in USB `class 07` → `/dev/usb/lp0` trên Linux.
- **Máy in biên nhận 80 mm để bàn** — Epson TM-T20/T88 (chế độ ESC/POS), Bixolon, Xprinter 80 mm.
  Đặt `FAXXME_WIDTH=48` và `FAXXME_PRINT_DOTS=576`.

Quy tắc bỏ túi: **nếu nó in được từ một app ESC/POS phổ thông, thì nó chạy với FaxxMe.**

## Cấu hình theo khổ giấy của bạn

| khổ giấy | `FAXXME_WIDTH` (cột chữ) | `FAXXME_PRINT_DOTS` (px ảnh) |
|-------|---------------------------|-------------------------------|
| 58 mm | `32` (mặc định) | `384` (mặc định) |
| 80 mm | `48` | `576` |

Nếu chữ xuống dòng lỗi hoặc ảnh quá hẹp/bị cắt, đây là hai thứ cần chỉnh.

## Chữ tiếng Việt / Unicode

Máy in nhiệt chỉ biết một bảng mã cũ (thường là CP437), nên chữ tiếng Việt có dấu (`ế ộ ậ ượ`),
emoji, CJK, v.v. không thể gửi thẳng dưới dạng byte — hầu hết máy in sẽ in ra `?`. FaxxMe xử lý việc
này tự động: một dòng **thuần ASCII** được in dưới dạng text ESC/POS gốc cho nhanh, còn một dòng có
**bất kỳ ký tự non-ASCII** nào (một dòng tin nhắn, hay một tên người gửi có dấu) được **dựng bằng
font đóng kèm và in thành một raster `GS v 0`** — nên nó chạy trên *mọi* máy in ESC/POS bất kể bảng mã
của nó là gì. Các tùy chỉnh:

| biến | mặc định | ý nghĩa |
|-----|---------|---------|
| `FAXXME_FONT` | Google Fonts **Play** đóng kèm | bất kỳ TTF nào có các glyph bạn cần |
| `FAXXME_FONT_SIZE` | `26` | to hơn = rõ hơn trên giấy nhiệt (nhưng ít chữ mỗi dòng hơn, tốn giấy hơn) |
| `FAXXME_FONT_THRESHOLD` | `176` | ngưỡng đen/trắng — tăng lên nếu chữ trông nhạt |

Chữ được dựng theo **ngưỡng, không dither**, nên nét chữ vẫn đặc và sắc.

## Tự cắt giấy

`FAXXME_CUT` điều khiển việc cắt giấy cuối mỗi bản fax (gửi dưới dạng một lệnh ESC/POS; máy in không
có dao cắt sẽ đơn giản bỏ qua):

- `full` (mặc định) — đẩy giấy một chút + cắt hết. An toàn ở mọi nơi.
- `feed` — đẩy tới dao cắt + cắt hết (`GS V 66`). **Sạch sẽ nhất & tốn ít giấy nhất, nhưng chỉ khi máy
  in thực sự có dao cắt** (nếu không nó sẽ không đẩy giấy ra để xé).
- `partial` — chừa lại một mẩu chưa cắt nhỏ.
- `none` — không cắt, chỉ đẩy giấy để xé tay.

Cách biết máy in của bạn có dao cắt hay không: gửi một bản fax thử với `FAXXME_CUT=full`. Nếu giấy bị
cắt thì nó có dao cắt (chuyển sang `feed`); nếu không thì nó chỉ để xé tay (giữ `full`).

## Lưu ý & các trường hợp không hỗ trợ

- **Máy in chỉ có Bluetooth/serial** — local bridge và WebUSB của FaxxMe nhắm vào USB. Một máy in chỉ
  phơi ra interface serial/Bluetooth SPP sẽ không với tới được (có thể thêm đường Web Serial — mở một
  issue nếu bạn cần).
- **Máy in rất cũ** chỉ hỗ trợ lệnh bit-image `ESC *` (không có `GS v 0`) sẽ in **chữ** tốt nhưng có
  thể bỏ qua hoặc làm rối **ảnh**.
- **Máy in GDI / "chỉ chạy bằng driver"** (nhiều máy in nhãn, máy in phun host-based) hoàn toàn không
  nhận ESC/POS thô — hãy dùng đường dự phòng **in qua trình duyệt** cho những máy này.
- **Kích thước ảnh** — ảnh cao bị giới hạn ở `FAXXME_IMG_MAX_H` dot; ảnh tải lên ở `FAXXME_MAX_UPLOAD`
  byte (6 MB). Việc dither diễn ra ở phía máy chủ, nên ảnh cực lớn chỉ tốn thêm chút CPU lúc gửi.

## Máy in không phải máy in nhiệt

Bất cứ thứ gì trình duyệt chiếm được qua WebUSB đều sẽ nhận các byte, nhưng một máy in phun/laser bình
thường sẽ không hiểu ESC/POS — với những máy đó hãy dùng đường dự phòng **in qua trình duyệt**, nó
dựng bản fax thành một biên nhận HTML rồi gửi qua hộp thoại in của OS tới bất kỳ máy in nào đã cài.
