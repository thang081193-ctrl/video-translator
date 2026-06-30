# PlantSmart 0630 — lỗi "bóp ảnh" (blur side-bar) + cách fix + danh sách file

> File này nằm TRONG git để PC nào pull repo cũng có đủ. Tổng hợp 2026-06-30.

## Triệu chứng
Một số video output 9:16 bị nội dung **bóp thành 1 dải hẹp ở giữa**, hai bên là
bar đen/blur (xem `003512` rõ nhất — chỉ còn 1 sliver mỏng).

## Nguyên nhân gốc (ĐÃ xác định — đừng debug lại từ đầu)
- Hàm `_detect_side_blur()` ở `pipeline/brand_pass.py` (~dòng 404) **báo nhầm
  (false-positive)** trên source 9:16 sạch (360×640) có chủ thể giữa nền trơn.
- Nó tưởng nền hai bên là "blur bar" → trả về crop dải giữa (vd src_003512 →
  `crop x=90 w=190` = 53% bề ngang) → dải đó được scale + blur-pad → frame hỏng.
- Điểm gọi: `pipeline/brand_pass.py` ~dòng 1275 (`side_blur = _detect_side_blur(...)`
  → set `pre_crop`).
- **Lưu ý:** edit `COVER_MAX_AR = 0.60` / `_should_cover` (brand_pass.py ~372, ~1292)
  **CHỈ là guard 1 phần, KHÔNG fix gốc** — nó chỉ đổi nhánh xử lý SAU khi đã crop sai.

## Hướng fix
1. Gate resize **THUẦN theo aspect ratio của FILE** (đọc dims trên đĩa): 9:16
   (≈0.5625) → fill full 1080×1920, **KHÔNG crop**. Bỏ/disable `_detect_side_blur`
   (cân nhắc cả `_detect_content_crop`) khỏi luồng mặc định — đừng đoán crop bằng pixel.
2. Giữ nuance cũ: source **RỘNG hơn** 9:16 (1:1, 4:5) vẫn blur-pad (bar nằm
   trên/dưới) để không chặt mất text burned-in full-width.
3. Render lại 26 file dưới đây (ghi đè đúng vị trí trong `_out/`). Source gốc lấy
   từ Drive/Vast (local thường không có). Voiced thì chạy lại QA voice-mix.

## Cách VERIFY (đã test, tái dùng được)
- Script: `scripts/audit_sidebar_blur.py <thư-mục-_out>` → in ra file nào còn lỗi.
- Quy tắc: trên file output, `edge_min` = min(Laplacian-variance dải ngoài 12%
  trái/phải); `band%` = bề rộng dải nội dung sắc nét giữa. **LỖI ⟺ `edge_min < 4.0`
  VÀ `band% ∈ (45, 90)`**. Đã verify: clip lỗi ≤2.7, clip sạch ≥6.15 (kể cả scene
  tối/nền trơn). Sau khi fix + render lại, `edge_min` phải lên ≥6 thì mới đạt.

## DANH SÁCH 26 FILE CẦN RENDER LẠI (7 source) — đường dẫn tương đối trong `_out/`

| source | angle | files |
|---|---|---|
| 003512 | care_reminder | BGM_UNIVERSAL/care_reminder/003512.mp4 · VOICED_English/care_reminder/EN-VO_000001.mp4 · VOICED_Portuguese/care_reminder/PT-VO_000001.mp4 · VOICED_Spanish/care_reminder/ES-VO_000001.mp4 |
| 003524 | care_reminder | BGM_UNIVERSAL/care_reminder/003524.mp4 · VOICED_English/care_reminder/EN-VO_000010.mp4 · VOICED_Portuguese/care_reminder/PT-VO_000010.mp4 · VOICED_Spanish/care_reminder/ES-VO_000010.mp4 |
| 003540 | discovery | BGM_UNIVERSAL/discovery/003540.mp4 · VOICED_English/discovery/EN-VO_000021.mp4 · VOICED_Portuguese/discovery/PT-VO_000021.mp4 · VOICED_Spanish/discovery/ES-VO_000021.mp4 |
| 003548 | care_reminder | BGM_UNIVERSAL/care_reminder/003548.mp4 · VOICED_English/care_reminder/EN-VO_000028.mp4 · VOICED_Portuguese/care_reminder/PT-VO_000028.mp4 · VOICED_Spanish/care_reminder/ES-VO_000028.mp4 |
| 003558 | plant_doctor | BGM_UNIVERSAL/plant_doctor/003558.mp4 · VOICED_English/plant_doctor/EN-VO_000035.mp4 · VOICED_Portuguese/plant_doctor/PT-VO_000035.mp4 · VOICED_Spanish/plant_doctor/ES-VO_000035.mp4 |
| 003580 | discovery | BGM_UNIVERSAL/discovery/003580.mp4 · VOICED_English/discovery/EN-VO_000039.mp4 · VOICED_Portuguese/discovery/PT-VO_000039.mp4 · VOICED_Spanish/discovery/ES-VO_000039.mp4 |
| 003520 | plant_doctor | VOICED_Portuguese/plant_doctor/PT_003520.mp4 · VOICED_Spanish/plant_doctor/ES_003520.mp4 (không có bản EN) |

Tổng: **26 file / 7 source**.
