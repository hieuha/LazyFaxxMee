# Tích hợp webhook

> 🌐 Ngôn ngữ: [English](../webhook.md) · **Tiếng Việt**

**Webhook inbound** cho phép bất kỳ site hay dịch vụ bên ngoài nào fax **cho bạn** — ví dụ ngay từ
ô comment của một blog (như [lazyblog](https://github.com/hieuha/lazyblog)) — mà người gửi **không
cần** tài khoản FaxxMe. Site gọi sẽ xác thực thay người gửi bằng **secret key của bạn**, và tin
nhắn được in ra máy in của bạn như mọi bản fax khác.

Đây là một webhook HTTP thuần: **ai giữ secret cũng có thể** `POST` một tin nhắn để in cho bạn. Vì
vậy secret chính là toàn bộ ranh giới bảo mật — hãy giữ nó ở phía server, và cứ xoay vòng/thu hồi
thoải mái.

```
 người xem          site của bạn (phía server)          FAXXME                 máy in của bạn
┌──────────┐  POST  ┌───────────────────────┐  POST    ┌────────────────┐  đẩy  ┌──────────┐
│ ô        │ ─────▶ │ blog / app / plugin    │ ───────▶ │ /api/fax/inbound│ ────▶ │  🖨      │
│ comment  │ (cùng  │ giữ FAXXME_SECRET_KEY   │ Bearer   │ xác thực bằng   │       └──────────┘
└──────────┘ origin)│ + tự kiểm tra spam      │ fxwh_…   │ secret → inbox  │
                    └───────────────────────┘          └────────────────┘
```

Lời gọi được thực hiện **phía server**, không phải từ trình duyệt người xem. Nhờ vậy secret luôn bí
mật, **khỏi cần CORS**, và site của bạn có thể tự thêm kiểm tra riêng cho từng người (captcha,
rate-limit của site) trước khi chuyển tiếp.

---

## Bắt đầu nhanh

1. **Tạo secret.** Đăng nhập FaxxMe → khối **`:: WEBHOOK INTEGRATION`** → **GENERATE SECRET KEY**.
   Key (`fxwh_…`) hiện dạng che; bấm **con mắt** để hiện rồi **copy**. Secret vẫn xem lại được trong
   khối bất cứ lúc nào.
2. **Lưu phía server** trên site gọi (ví dụ trong `.env` dưới tên `FAXXME_SECRET_KEY`). Đừng bao
   giờ đưa nó ra trình duyệt hay commit vào git.
3. **POST một tin nhắn** tới `/api/fax/inbound` kèm header `Authorization: Bearer <secret>`. In ra.

```bash
curl -X POST https://fax.hatrunghieu.com/api/fax/inbound \
  -H "Authorization: Bearer fxwh_XXXXXXXX" \
  --data-urlencode "body=Bài viết hay quá!" \
  --data-urlencode "name=Một độc giả" \
  --data-urlencode "post=Bài fax đầu tiên" \
  --data-urlencode "url=https://blog.example/first"
```

---

## Tham chiếu endpoint

### `POST /api/fax/inbound`

- **Xác thực:** header `Authorization: Bearer <secret key>` (giá trị `fxwh_…`). **Không** dùng
  cookie phiên — người gửi không có tài khoản.
- **Body:** `application/x-www-form-urlencoded` (form HTML chuẩn).

| trường | bắt buộc | tối đa | ghi chú |
|--------|:--------:|:------:|---------|
| `body` | ✅ | `FAXXME_WEBHOOK_MSG_MAX` (500) | tin nhắn được in |
| `name` | – | 40 | tên người gửi — in kèm để ghi nguồn (`— name`) |
| `post` | – | 120 | tiêu đề nguồn, ví dụ tên bài viết |
| `url` | – | 200 | URL nguồn |

> **Không có trường IP** — FaxxMe tự suy ra IP client (từ kết nối / header CF/reverse-proxy) để
> rate-limit theo IP. IP đó là server **site gọi** của bạn, không phải người xem cuối, vì vậy hãy
> tự thêm throttle theo từng người xem (xem [Bảo mật](#bảo-mật--bảo-vệ)).

### Phản hồi

`200 OK`

```json
{ "ok": true, "fax_id": 123, "delivered": true }
```

`delivered` là `true` nếu có máy in (tab trình duyệt, agent trên Pi, hoặc bridge trên host) nhận
ngay, hoặc `false` nếu bản fax được **xếp hàng** và sẽ in khi máy in của bạn kết nối lại — dù thế
nào thì bản fax vẫn được nhận và lưu.

### Lỗi

| mã | ý nghĩa |
|:--:|---------|
| `401` | `missing webhook secret` (thiếu/sai header Bearer) hoặc `invalid webhook secret` (không tồn tại/đã thu hồi) |
| `400` | `empty message` (rỗng) hoặc `message too long (max 500)` (quá dài) |
| `429` | bị giới hạn — quá nhiều fax inbound cho tác giả này hoặc từ IP site gọi này |
| `404` | route không tồn tại → **server đang chạy code cũ, hãy restart** (xem [Khắc phục sự cố](#khắc-phục-sự-cố)) |

---

## Ví dụ request

**PHP** (ví dụ plugin blog — secret nằm lại phía server):

```php
<?php
$faxxme = 'https://fax.hatrunghieu.com';
$secret = getenv('FAXXME_SECRET_KEY');   // fxwh_… , giữ ngoài version control

$ch = curl_init("$faxxme/api/fax/inbound");
curl_setopt_array($ch, [
    CURLOPT_POST           => true,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTPHEADER     => ["Authorization: Bearer $secret"],
    CURLOPT_POSTFIELDS     => http_build_query([
        'body'      => $_POST['message'] ?? '',
        'name'      => $_POST['name'] ?? '',
        'post'      => $postTitle,
        'url'       => $postUrl,
    ]),
    CURLOPT_TIMEOUT        => 10,
]);
$res  = curl_exec($ch);
$code = curl_getinfo($ch, CURLINFO_HTTP_CODE);   // 200 ok · 429 quá nhanh · 401 sai secret
curl_close($ch);
```

**Python** (`requests`):

```python
import os, requests

r = requests.post(
    "https://fax.hatrunghieu.com/api/fax/inbound",
    headers={"Authorization": f"Bearer {os.environ['FAXXME_SECRET_KEY']}"},
    data={
        "body": message,
        "name": reader_name,
        "post": post_title,
        "url": post_url,
    },
    timeout=10,
)
r.raise_for_status()
```

**Node.js** (phía server; đừng lộ secret trong JS front-end):

```js
const body = new URLSearchParams({
  body: message, name, post: postTitle, url: postUrl,
});
const r = await fetch("https://fax.hatrunghieu.com/api/fax/inbound", {
  method: "POST",
  headers: { Authorization: `Bearer ${process.env.FAXXME_SECRET_KEY}` },
  body,
});
// r.status: 200 ok · 401 sai secret · 429 quá nhanh
```

---

## Bảo mật & bảo vệ

- **Chỉ gọi phía server.** Secret là credential kiểu bearer — ai có nó cũng in được cho bạn. Giữ nó
  trên server (biến môi trường / secrets manager), tuyệt đối không để trong JS trình duyệt, app di
  động, hay git. Vì gọi server-to-server nên **không cần cấu hình CORS**.
- **Luôn dùng HTTPS.** Bearer token qua HTTP thường có thể bị nghe lén. Endpoint production đã là
  HTTPS (Cloudflare + Caddy); đừng hạ xuống HTTP.
- **Phạm vi hẹp theo thiết kế.** Một secret **chỉ** gửi fax được cho đúng tác giả sở hữu nó. Không
  có **trường người nhận** — secret bị lộ chỉ có thể spam cuộn giấy *của bạn*, không gì khác.
- **Rate-limit hai tầng.** FaxxMe giới hạn fax inbound **theo tác giả (secret)** và **theo IP site
  gọi**. Giới hạn theo tác giả là lớp chính, không bypass được. Giới hạn theo IP dùng IP client mà
  FaxxMe suy ra từ kết nối / header reverse-proxy: đáng tin **khi sau proxy tin cậy** (Cloudflare/
  Caddy ghi đè các header đó), nhưng nếu origin truy cập trực tiếp được thì có thể bị spoof — nên
  coi là "best-effort". FaxxMe chỉ thấy server của bạn, không thấy người xem cuối, nên site *cũng
  nên* tự rate-limit / captcha từng người xem.
- **Nội dung được làm sạch trước khi in.** Byte điều khiển (ESC/GS…) trong message, tên người gửi,
  hay nguồn đều bị loại tại ranh giới render, nên nội dung webhook không thể inject lệnh ESC/POS thô
  vào máy in.
- **In ngay (fire-and-forget).** Tin được nhận sẽ in luôn, người gửi ghi là tài khoản dành riêng
  `@webhook`. Không có bước duyệt của người, nên các biện pháp chống lạm dụng ở trên rất quan trọng.
  Nếu bị spam, **thu hồi secret** — có hiệu lực tức thì.
- **Kiểm tra trước khi chuyển tiếp.** Cắt/giới hạn độ dài, loại spam rõ ràng, và loại ký tự điều
  khiển ở phía bạn. FaxxMe giới hạn `body` ở `FAXXME_WEBHOOK_MSG_MAX` và dựng Unicode (tiếng Việt,
  emoji) thành raster font, nhưng **không** lọc nội dung.

### Điều chỉnh giới hạn (người vận hành server)

| biến môi trường | mặc định | ý nghĩa |
|-----------------|:--------:|---------|
| `FAXXME_WEBHOOK_RATE_MAX` | `5` | số fax inbound tối đa mỗi cửa sổ, theo tác giả **và** theo IP site gọi (0 = tắt) |
| `FAXXME_WEBHOOK_RATE_WINDOW` | `300` | độ dài cửa sổ, tính bằng giây |
| `FAXXME_WEBHOOK_MSG_MAX` | `500` | số ký tự tối đa trong một tin nhắn inbound |

Đặt các biến này trong môi trường của service FaxxMe (xem [Cơ chế hoạt động](how-it-works.md) và
bảng cấu hình trong [README](../../README-vi.md)), rồi restart service.

---

## Quản lý secret key của bạn

Tất cả nằm trong khối **`:: WEBHOOK INTEGRATION`** sau khi đăng nhập:

| hành động | endpoint | tác dụng |
|-----------|----------|----------|
| **Tạo / tạo lại** | `POST /api/webhook/regenerate` | cấp `fxwh_…` mới. **Tạo lại là vô hiệu hóa ngay secret cũ** — mọi site đang dùng nó sẽ nhận `401` cho tới khi bạn cập nhật. |
| **Xem lại** | — | khối hiển thị secret dạng che (`••••`); bấm **con mắt** để hiện, bấm **copy** để sao chép. Secret vẫn xem lại được nên bạn không cần lưu ở nơi khác. |
| **Thu hồi** | `POST /api/webhook/revoke` | xóa hẳn secret; webhook tắt cho tới khi bạn tạo cái mới. |
| **Kiểm tra** | `GET /api/me` → `webhook_secret` | secret hiện tại (giá trị của chính chủ), hoặc `null` nếu chưa đặt. |

Hãy xoay secret định kỳ, và mỗi khi nghi nó bị lộ. Secret được lưu **plaintext** phía server (cột
`users.webhook_secret`) để khối có thể hiện lại — đánh đổi để xem lại được. Nó chỉ cho phép ai đó
spam-in tới bạn và có thể thu hồi tức thì nên rủi ro thấp; dù vậy hãy bảo vệ quyền truy cập DB và
bản backup cho phù hợp.

### Quản trị (người vận hành server)

- Xóa một user (tombstone trong trang admin) sẽ ẩn danh tài khoản và xóa webhook secret (cùng
  device token); tài khoản đã tombstone cũng bị loại khỏi lookup webhook, nên webhook của nó ngừng
  hoạt động ngay lập tức.
- Tài khoản gửi `webhook` là **tài khoản hệ thống dành riêng**: được tạo ở lần fax inbound đầu tiên,
  không đăng nhập được, và ẩn khỏi ô chọn callsign. Không ai đăng ký được callsign `webhook`.
- Fax inbound xuất hiện trong inbox người nhận và trong danh sách fax của admin như mọi bản fax
  khác, với người gửi `@webhook`.

---

## Bản fax in ra trông thế nào

Header người gửi luôn là tài khoản hệ thống — `FROM: Webhook @webhook` — message in cỡ thường, còn
khối ghi nguồn (`name`, `post`, `url`) in **chữ nhỏ hơn** ở dưới (cỡ theo `FAXXME_FOOTER_FONT_SIZE`,
mặc định 22):

```
        FAXXME
--------------------------------
FROM: Webhook @webhook
TIME: 2026-07-08 16:45:02
--------------------------------
Bài viết hay quá!

— Một độc giả
Bài fax đầu tiên
https://blog.example/first
--------------------------------
      .: end of message :.
```

Dòng ASCII in bằng text ESC/POS gốc; dòng nào có tiếng Việt/emoji sẽ tự động được dựng thành raster
font.

---

## Công thức tích hợp (ô comment blog)

Vì gọi phía server, bạn có thể gắn tính năng này vào bất cứ thứ gì chạy code trên server. Một luồng
comment blog điển hình:

1. Thêm một form nhỏ dưới mỗi bài: ô tin nhắn, tên (tùy chọn), và nút gửi `POST` về **server của
   chính bạn** (cùng origin — không để URL FaxxMe lộ trong trang).
2. Trên server của bạn, xử lý submit đó: kiểm tra + lọc spam, rồi chuyển tiếp tới
   `POST {FAXXME}/api/fax/inbound` với header `Authorization: Bearer` và các trường ở trên, truyền
   tiêu đề/URL bài viết vào `post`/`url`.
3. Trả về thông báo thân thiện "đã fax!" / "chậm lại nhé" cho người xem dựa trên `200`/`429`.

Riêng với **lazyblog** (PHP, có hệ thống plugin), việc này nằm gọn trong một plugin: plugin render
form và xử lý chuyển tiếp phía server, với `FAXXME_SECRET_KEY` trong `.env` của blog. Đoạn PHP ở
trên chính là phần lõi.

---

## Khắc phục sự cố

| hiện tượng | nguyên nhân & cách sửa |
|------------|------------------------|
| **`404 Not Found`** trên `/api/webhook/*` hoặc `/api/fax/inbound` | server đang chạy code cũ hơn tính năng này. **Restart** để nạp route mới: `sudo systemctl restart faxxme` (hoặc lệnh restart của bản triển khai). Route sống trả `401`, không phải `404`. |
| **`401 invalid webhook secret`** | secret sai/cũ/đã thu hồi. Tạo lại trong khối và cập nhật site gọi. |
| **`401 missing webhook secret`** | header `Authorization: Bearer …` không tới nơi (proxy cắt mất? sai tên header?). |
| **`429`** | chạm giới hạn — vượt `FAXXME_WEBHOOK_RATE_MAX` mỗi cửa sổ cho tác giả này hoặc IP site gọi này. Giãn ra, hoặc nâng giới hạn phía server. |
| **UI vẫn ghi `BLOG INTEGRATION` / nhãn cũ** | hard-refresh trang (`Ctrl+Shift+R`) để nạp `app.js`/`index.html` mới. |
| Fax được nhận (`200`) nhưng không in ra | `delivered:false` nghĩa là đã xếp hàng — sẽ in khi máy in/agent/bridge của bạn kết nối lại. Kiểm tra pill trạng thái máy in. |

Xem thêm: [Cơ chế hoạt động](how-it-works.md) · [Tương thích máy in](printers.md) ·
[README](../../README-vi.md).
