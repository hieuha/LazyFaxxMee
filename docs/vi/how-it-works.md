# FaxxMe hoạt động thế nào

> 🌐 Ngôn ngữ: [English](../how-it-works.md) · **Tiếng Việt**

FaxxMe tái hiện cái cảm giác hồi hộp ngày xưa khi fax cho một người bạn: bạn gõ một tin nhắn (và có
thể đính kèm một tấm ảnh), rồi nó in ra trên chiếc máy in nhiệt vật lý của **họ** — ngay lập tức nếu
họ đang ở đó, hoặc đúng lúc máy in của họ trở lại nếu không.

```
  người gửi                       máy chủ FAXXME (FastAPI)            người nhận in qua…
 ┌────────────┐  POST /api/fax  ┌────────────────────────┐  WS push  ┌────────────────────────┐
 │  ô soạn tin│ ──────────────▶ │  deliver(fax):         │ ────────▶ │ trình duyệt (WebUSB) 🖨 │
 └────────────┘                 │   1. người nhận online?│           │ hoặc agent trên Pi   🖨 │
   ▲ "đã in" (WS)               │   2. local bridge host │           └────────────────────────┘
   └────────────────────────────│   3. còn lại → hàng đợi │  …hoặc local bridge host → /dev/usb/lp0
                                └───────────┬────────────┘  (tiến trình nền xả khi cắm lại)
                                   SQLite ◀─┘
```

## Các thành phần

| file | vai trò |
|------|------|
| `faxxme/app.py` | route FastAPI, presence WebSocket, logic giao fax, canh máy in, device token |
| `faxxme/db.py` | SQLite (stdlib): `users` (+ băm device-token) + `faxes` (BLOB ảnh đã dither, cờ xóa từng phía) |
| `faxxme/auth.py` | mật khẩu pbkdf2 + cookie phiên hmac + device token (không phụ thuộc native) |
| `faxxme/printer.py` | dựng biên nhận ESC/POS, tự cắt, và local bridge in ra `/dev` |
| `faxxme/imaging.py` | ảnh → halftone 1-bit Floyd–Steinberg → raster `GS v 0` (Pillow) |
| `static/` | UI CRT một trang (client WebUSB + WebSocket) |
| `agent/` | agent node-in chạy nền cho Raspberry Pi (xem [../../agent/README-vi.md](../../agent/README-vi.md)) |

## Presence — ai đang "online"

Một người dùng **online** đúng khi họ đang có một WebSocket (`/ws`) mở — tức là tab console đang mở.
Máy chủ giữ một map trong bộ nhớ `user_id → {sockets}`. Người dùng online:

- nhận fax được đẩy tới theo thời gian thực,
- hiện chấm xanh trong ô tìm người nhận của mọi người,
- nhận cập nhật trạng thái trực tiếp (`queued → printed`).

Presence **không** được lưu bền; nó thuần túy là "ngay lúc này có socket nào đang kết nối không". Máy
chủ cũng theo dõi, theo từng người dùng, xem có socket nào là **agent** hay không (`node_online`) để
web UI hiển thị `PRINTER: NODE ✓`.

## Gửi một bản fax

`POST /api/fax` (multipart: `to`, `body`, `image` tùy chọn) chạy `deliver(fax)`, thử ba việc theo
thứ tự:

1. **Người nhận online (WebSocket)** → máy chủ đẩy bản fax (kèm các byte ESC/POS in-được-ngay, dạng
   base64) qua socket của họ. Client — một **trình duyệt** (WebUSB) *hoặc* một **agent trên Pi** —
   ghi các byte ra máy in rồi gửi **ack**; máy chủ đánh dấu `delivered`.
2. **Local bridge trên host** → nếu callsign người nhận trùng với `FAXXME_LOCAL_USER` và thiết bị máy
   in của host đang ghi được, máy chủ tự in các byte ra `FAXXME_PRINTER_DEV` (vd `/dev/usb/lp0`) và
   đánh dấu `delivered`. Không cần trình duyệt.
3. **Cả hai đều không** → bản fax nằm lại `pending` trong SQLite.

## Giao khi quay lại

Một bản fax đang chờ rời khỏi hàng đợi khi:

- **người nhận kết nối lại** (trình duyệt *hoặc* agent trên Pi) — ngay khi WebSocket kết nối, máy chủ
  xả mọi bản fax `pending` cho họ; hoặc
- **máy in trên host xuất hiện lại** — một **tiến trình nền canh chừng** kiểm tra `FAXXME_PRINTER_DEV`
  mỗi `FAXXME_PRINTER_POLL` giây (mặc định 4). Khi thiết bị lại ghi được (vd sau khi rút/cắm lại), nó
  in các bản fax đang chờ của người dùng local-bridge và đẩy một thông điệp `status` để hộp đi của
  người gửi lật `queued → printed` mà không cần tải lại trang.

Tiến trình canh chừng cũng chạy một lần lúc khởi động, nên một lần reboot cũng xả hết những gì đang
xếp hàng.

Ở phía **trình duyệt/WebUSB** có một cơ chế tương đương chạy phía client: quyền WebUSB được lưu lại,
nên FaxxMe tự động bind lại một máy in đã được cấp quyền trước đó khi tải trang, và lắng nghe các sự
kiện USB `connect`/`disconnect` — rút/cắm lại máy in là nó tự bind lại và in ra các bản fax đang chờ,
không cần bấm *CONNECT PRINTER*.

## Node in (agent) & device token

Thay vì trình duyệt, người dùng có thể chạy một **agent** chạy nền trên chiếc Raspberry Pi của mình.
Nó chỉ đơn giản là thêm một client WebSocket, nên toàn bộ mô hình giao fax bên trên hoạt động y
nguyên — agent nhận đúng những thông điệp `fax` đó và ghi các byte ESC/POS ra máy in cục bộ của nó.

- **Xác thực.** `/ws` chấp nhận hoặc một **cookie phiên** (trình duyệt) hoặc một **device token**
  (agent), gửi qua `Authorization: Bearer <token>` + `X-Faxxme-User: <callsign>`. Token là một chuỗi
  entropy cao được lưu **băm sha256** trong `users.token_hash`; `POST /api/token/regenerate` cấp một
  token mới (chỉ trả về một lần) và **ngắt ngay bất kỳ agent nào đang kết nối bằng token cũ** — thu
  hồi tức thì.
- **Chỉ báo node.** Khi một agent kết nối/ngắt, máy chủ phát một thông điệp `{type:node}` tới các tab
  trình duyệt của người dùng đó, và pill PRINTER cập nhật trực tiếp
  (`ONLINE` USB-trình-duyệt → `NODE ✓` → `WIRED` → `OFFLINE`). Trong lúc một node/bridge in cho bạn,
  nút *CONNECT PRINTER* trên trình duyệt bị ẩn đi (bạn không cần WebUSB).
- **Thử in.** `POST /api/test-print` đẩy một biên nhận thử do hệ thống tạo tới agent của bạn (hoặc
  local bridge trên host) để **TEST** vẫn chạy được ngay cả khi chưa bind máy in qua trình duyệt.

## Một nguồn sự thật duy nhất: ESC/POS dựng ở máy chủ

Các byte biên nhận luôn được dựng ở máy chủ (`printer.build_receipt`). Trình duyệt không bao giờ tự
định dạng gì cả — nó chỉ chuyển tiếp các byte thô qua WebUSB. Local bridge ghi *đúng* những byte đó ra
thiết bị. Điều này giữ cho WebUSB và local bridge giống nhau từng byte một, và biến mọi thay đổi bố
cục/định dạng thành việc sửa đúng một file.

Bố cục biên nhận:

```
        FAXXME            (cỡ đôi, căn giữa)
--------------------------------
FROM: <tên hiển thị> @<callsign>
TIME: YYYY-MM-DD HH:MM:SS
--------------------------------
<nội dung tin nhắn, tự xuống dòng theo FAXXME_WIDTH>
[raster ảnh đã dither, nếu có đính kèm]
--------------------------------
     .: end of message :.
<đẩy giấy / cắt theo FAXXME_CUT>
```

**Chữ Unicode.** Máy in nhiệt chỉ biết một bảng mã cũ, nên tiếng Việt/emoji không thể gửi thẳng dưới
dạng byte. `build_receipt` kiểm tra từng dòng: **ASCII** giữ dạng text ESC/POS gốc cho nhanh; còn lại
(một dòng nội dung, hay một tên người gửi có dấu) được dựng bằng font đóng kèm (`FAXXME_FONT`, Google
Fonts "Play") và in thành một **raster `GS v 0` sắc nét theo ngưỡng** (`imaging.text_raster`) — không
dither, nên nét chữ vẫn đặc và liền.

## Ảnh đính kèm

Tùy chọn. Ở phía client bạn chọn một tấm ảnh và thấy ngay một **bản xem trước Floyd–Steinberg trực
tiếp** (canvas). Khi gửi, máy chủ (`imaging.process_upload`):

1. sửa hướng xoay theo EXIF, chuyển sang thang xám, tự tăng tương phản,
2. co về đúng khổ giấy (`FAXXME_PRINT_DOTS`, mặc định 384 ≈ 58mm), giới hạn chiều cao,
3. **dither Floyd–Steinberg thành 1-bit** và lưu một PNG gọn trong bản ghi fax.

Lúc in, `imaging.escpos_raster` đóng gói PNG đó thành một lệnh raster `GS v 0` đặt bên dưới phần chữ.
Cũng chính PNG đó được phục vụ tại `GET /api/fax/{id}/image` để hiển thị lên màn hình (chỉ người
gửi/người nhận).

## Dọn dẹp

- **Hộp đến/hộp đi** hiển thị 50 bản mới nhất của bạn; các bản cũ hơn được tự dọn theo từng phía.
- **Dọn (Clear)** chỉ ẩn fax khỏi *phía bạn* (cờ soft-delete `sender_deleted` / `recipient_deleted`);
  phía bên kia vẫn giữ bản của họ. Một bản ghi chỉ bị xóa vật lý khi *cả hai phía* đều đã dọn.
- Bạn **không thể tự fax cho mình**; tin nhắn giới hạn 200 ký tự.

## Cửa sổ xem bản in

Bấm vào bất kỳ bản fax nào trong hộp đến/hộp đi để thấy nó hiện ra như một **tờ giấy in** — giấy màu
kem, mép rách zigzag, đúng bố cục chữ mà máy in tạo ra. Tiện để đọc lại hoặc chụp màn hình.

## Trang quản trị (admin)

`/admin` là một "phòng điều khiển" tùy chọn, **tách rời khỏi tài khoản người dùng**: nó được mở khóa
bằng một mật khẩu duy nhất mà hash sha256 của nó nằm trong `FAXXME_ADMIN_PASSWORD_HASH` (không đặt ⇒
tắt hẳn trang). Không có tài khoản admin, không thêm bảng nào.

- **Xác thực.** `POST /api/admin/login` so mật khẩu với hash trong env rồi đặt một cookie admin ký
  bằng hmac (`fx_admin`) — phiên riêng, tách khỏi `fx_session` của người dùng. Mọi route
  `/api/admin/*` đều kiểm cookie này và trả `401` nếu thiếu, nên trang tĩnh phục vụ cho ai cũng vô
  hại; chỉ dữ liệu mới bị chặn. `POST /api/admin/logout` xóa cookie.
- **Quản lý gì.** **Thống kê** trực tiếp; danh sách **operator** phân trang (20/trang) kèm số fax
  gửi/nhận và trạng thái online/node/token — thu hồi device token (ngắt agent) hoặc xóa người dùng;
  và danh sách **toàn bộ tin nhắn** phân trang, tìm kiếm được — xem một bản dưới dạng tờ giấy in
  (đầy đủ nội dung + ảnh qua `/api/admin/faxes/{id}/image`) hoặc xóa hẳn cả hai phía.
- **Xóa operator là "tombstone", không phải xóa sạch.** Thay vì xóa dòng user (sẽ kéo theo xóa mọi
  fax user đó dính tới và cuốn luôn bản sao của *đối phương*), tài khoản được **ẩn danh tại chỗ**:
  `db.tombstone_user` đổi tên thành `deleted_<id>`, xóa mật khẩu + device token, và ghi `deleted_at`.
  Fax vẫn hợp lệ (khóa ngoại còn nguyên) và vẫn hiển thị cho đối phương dưới dạng tài khoản
  `deleted_<id>`; user biến khỏi roster và ô chọn người nhận, không đăng nhập được nữa, còn callsign
  gốc thì được giải phóng để đăng ký lại (tiền tố `deleted_` được giữ chỗ nên không bao giờ trùng).
- **Dùng chung presence.** Cột online / node đọc *đúng* map presence trong RAM mà phần còn lại của
  app dùng, nên một tab trình duyệt hoặc agent Pi kết nối tới **server này** sẽ hiện ngay ở đây (còn
  agent trỏ tới server khác thì không — presence là theo từng server, lưu trong RAM).
