# Triển khai FaxxMe như một dịch vụ

> 🌐 Ngôn ngữ: [English](README.md) · **Tiếng Việt**

Chạy FaxxMe như một dịch vụ **systemd** (`python -m faxxme` dưới uvicorn), thiết lập quyền truy cập
máy in, và đẩy log vào journal.

## Cài đặt

```bash
sudo deploy/install.sh
```

Cần Python 3.10+. Trên Debian/Ubuntu, script tự cài `python3-venv` + `python3-pip` nếu chúng còn
thiếu (qua `apt`); trên các distro khác hãy tự cài chúng trước.

Việc này sẽ:
1. Tạo virtualenv (`.venv`) và cài `requirements.txt`.
2. Cài một udev rule để các máy in nhiệt USB (`/dev/usb/lp*`) ghi được bởi nhóm `lp`, và thêm user
   của bạn vào nhóm đó.
3. Sao chép `faxxme.env.example` → `faxxme.env` (file cấu hình bạn chỉnh được).
4. Dựng và cài `/etc/systemd/system/faxxme.service`.
5. Bật nó (khởi động cùng máy) và chạy ngay.

Chạy lại bất cứ lúc nào sau khi pull code mới hoặc đổi cấu hình — nó idempotent (chạy lại nhiều lần
vẫn an toàn).

## Quản lý dịch vụ

```bash
sudo systemctl start faxxme        # khởi động
sudo systemctl stop faxxme         # dừng
sudo systemctl restart faxxme      # khởi động lại (sau khi đổi code/cấu hình)
systemctl status faxxme            # đang chạy không?
sudo systemctl disable faxxme      # không khởi động cùng máy
sudo systemctl enable faxxme       # khởi động cùng máy
```

## Log

```bash
journalctl -u faxxme -f            # theo dõi trực tiếp
journalctl -u faxxme -n 100        # 100 dòng cuối
journalctl -u faxxme --since "10 min ago"
```

## Cấu hình

Sửa `deploy/faxxme.env`, rồi khởi động lại:

```bash
sudoedit deploy/faxxme.env
sudo systemctl restart faxxme
```

| Biến | Mặc định | Ý nghĩa |
|----------|---------|---------|
| `FAXXME_HOST` / `FAXXME_PORT` | `0.0.0.0` / `8000` | địa chỉ bind |
| `FAXXME_LOG_LEVEL` | `info` | mức log của uvicorn |
| `FAXXME_LOCAL_USER` | `pi` | callsign có fax được in trên máy in của host này |
| `FAXXME_PRINTER_DEV` | `/dev/usb/lp0` | node thiết bị máy in |
| `FAXXME_PRINTER_POLL` | `4` | số giây giữa các lần kiểm tra cắm-lại-nóng máy in |
| `FAXXME_WIDTH` / `FAXXME_PRINT_DOTS` | `32` / `384` | bề rộng biên nhận (58mm) |
| `FAXXME_CUT` | `full` | cắt giấy cuối mỗi bản fax: `full` / `feed` (đẩy tới dao cắt) / `partial` / `none` |
| `FAXXME_FAX_RATE_MAX` / `FAXXME_FAX_RATE_WINDOW` | `20` / `60` | chống spam: tối đa N bản fax mỗi N giây (0 = tắt) |

[README](../README-vi.md#cấu-hình) chính liệt kê đầy đủ (giới hạn ảnh, tinh chỉnh font Unicode, đường
dẫn DB/secret).

## Gỡ cài

```bash
sudo deploy/uninstall.sh           # gỡ dịch vụ + udev rule; giữ code/db/venv
```

## Ghi chú

- `deploy/faxxme.env` bị git bỏ qua (đặc thù từng host). `faxxme.env.example` là file mẫu.
- Log đi vào journal vì uvicorn ghi ra stdout/stderr.
- Để các trình duyệt ở xa dùng được WebUSB thì bạn vẫn cần HTTPS (vd `tailscale serve`); còn máy in
  local-bridge thì chạy được qua plain HTTP.
