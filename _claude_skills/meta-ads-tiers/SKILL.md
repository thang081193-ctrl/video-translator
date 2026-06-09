---
name: meta-ads-tiers
description: Meta Ads kit for IAA (in-app advertising) apps — country tiers (T1/T2/T3), bulk-add paste blocks, AND end-to-end creative kit generation (Primary text + Headlines) for a specific app. TRIGGER when user (a) asks for "tier list", "tier 1", "tier 2", "list nước chạy ads", "countries for Meta Ads", (b) pastes a Play Store / App Store URL + a video folder path and asks for "ads", "primary + headline", "copy", "creative", "campaign setup", "làm ads cho app này", or (c) wants paste-ready strings for Meta Ads' bulk-add-locations modal. Output names match Meta's autocomplete exactly. Output Primary/Headlines as copy-paste-friendly markdown tables.
---

# Meta Ads kit — IAA apps

Two modes:

- **Mode A: tier-list lookup** — user asks for country lists or paste blocks. Jump to §1.
- **Mode B: full creative kit** — user gives an app URL + a folder of videos and asks for ads/copy. Run §3 workflow end-to-end, then output §1 countries + §2 ROAS guidance + Primary/Headlines tables from §3.

Tier 1/2/3 is not a Meta-defined standard — it's the working classification used by the user's UA team for IAA monetization (Audience Network + AdMob eCPM driven). The country names below match Meta's bulk-add autocomplete exactly so they can be pasted as-is.

---

## §1 — Country tiers (paste-ready)

Ads Manager → Ad set → Locations → "Add locations in bulk" → Location type: **Countries** → paste comma-separated string → Match locations.

### Tier 1 (8 nước) — eCPM cao nhất, ~70-80% spend

```
United States, Canada, United Kingdom, Australia, New Zealand, Germany, France, Japan
```

### Tier 2 (21 nước) — eCPM trung-cao, scale tốt

```
Italy, Spain, Portugal, Netherlands, Belgium, Switzerland, Austria, Ireland, Sweden, Norway, Denmark, Finland, South Korea, Singapore, Taiwan, Hong Kong, Israel, United Arab Emirates, Saudi Arabia, Poland, Czechia
```

### Sub-cluster theo ngôn ngữ creative

**EN — T1 anglo:**
```
United States, Canada, United Kingdom, Australia, New Zealand
```

**EN — T2 (Bắc Âu + IE + SG + HK + IL + KR + TW + UAE chấp nhận EN):**
```
Ireland, Netherlands, Sweden, Norway, Denmark, Finland, Singapore, Hong Kong, Israel, South Korea, Taiwan, United Arab Emirates
```

**EN combined (T1 anglo + T2 EN-accepting) — dùng cho ROAS-opt 1 ad set:**
```
United States, Canada, United Kingdom, Australia, New Zealand, Ireland, Netherlands, Sweden, Norway, Denmark, Finland, Singapore, Hong Kong, Israel, South Korea, Taiwan, United Arab Emirates
```

**DE creative:**
```
Germany, Austria, Switzerland
```

**FR creative:**
```
France, Belgium, Switzerland, Canada
```

**ES creative (Spain only — Latam là T3):**
```
Spain
```

**IT creative:**
```
Italy
```

**PT creative (Portugal only — Brazil là T3):**
```
Portugal
```

**AR creative:**
```
United Arab Emirates, Saudi Arabia
```

**T3 vẫn chạy được:**
```
Poland, Czechia, Turkey, Greece, Hungary
```

### Tên Meta autocomplete cần nhớ

- `United States`, không phải "USA"/"US"
- `United Kingdom`, không phải "UK"
- `United Arab Emirates`, không phải "UAE"
- `South Korea`, không phải "Korea"
- `Hong Kong` (2 từ)
- `Czechia`, không phải "Czech Republic"
- `Saudi Arabia` (2 từ)
- `New Zealand` (2 từ)

---

## §2 — Ad set strategy theo objective

### CBO/ABO + install-opt (Lowest cost)
**Chia tier riêng.** Mỗi tier 1 ad set. Trộn → Meta dump spend vào nước CPI rẻ, ROAS méo.

### Advantage+ App Campaign (AAC) + ROAS / Value opt
**GỘP chung 1 ad set rộng** (T1 anglo + T2 EN-accepting). Lý do:
- ROAS bidding cần volume value signal (purchase_value postbacks qua AEM). Chia ad set = chia signal = learning phase lâu.
- Algorithm tự tìm high-LTV user bất kể quốc gia.
- Meta khuyến nghị 1 ad set rộng > nhiều ad set hẹp khi dùng Value Opt.

**Khi nào tách dù dùng ROAS:**
- Budget < $30-50/day → focus T1 only
- Sau 5-7 ngày: 1 nước ăn >40% spend mà ROAS thấp → tách nước đó ra
- Test creative ngôn ngữ khác (DE/FR/JP) → tách theo ngôn ngữ, không theo tier

**Min-ROAS:** 3-5 ngày đầu không set (cho algo học). Sau đó set min-ROAS = 30-50% target để khóa floor.

---

## §3 — Mode B workflow: tự sinh Primary + Headlines từ app + video

Trigger: user dán Play Store/App Store URL **và** folder path chứa creative videos.

### Bước 1 — Đọc context app

- Lấy package name / app ID từ URL (ví dụ `ai.home.design.remodel.interior`)
- Tách keyword từ package name: domain (home design / interior / plant / fitness…), USP (AI, free, scanner…)
- Nếu user nói app là IAA + có lifecycle D0-D3 → ưu tiên hook **install volume**, **free**, **quick demo** (D0 retention = ad impressions = revenue)

### Bước 2 — Sample frame từ videos

List folder, chọn ~6 video đại diện rải đều (đầu/giữa/cuối batch). Trên Windows:

```bash
mkdir -p "D:/tmp/madskit_frames"
for v in <list 6 video stems>; do
  ffmpeg -ss 2 -i "<folder>/${v}.mp4" -frames:v 1 -q:v 3 "D:/tmp/madskit_frames/${v}.jpg" -y 2>/dev/null
done
```

Đọc từng frame bằng `Read` tool — Claude là vision model, xem được on-screen text + bố cục.

**Tip:** lấy frame ở giây 2-3 để bắt được hook text (thường hiện 1-3s đầu). Nếu frame nào blur/empty, lấy thêm frame ở giây 5 hoặc 8.

### Bước 3 — Phân loại creative pattern

Đánh tag mỗi video theo hook pattern:

| Pattern | Ví dụ on-screen text |
|---|---|
| **Pain → solution** | "Stop living in a boring home. Use AI." |
| **Decision paralysis** | "🔥 Help me! I can't decide" / "Boho, modern, japandi?" |
| **Gender bait** | "Men can't decorate" / "My husband picked this 🤡" |
| **Couple/family relatable** | "My wife couldn't decorate in 30 years, AI did in 3 sec" |
| **Speed promise** | "in 3 seconds" / "instantly" |
| **Before/after reveal** | Empty/blurred → rendered |
| **Aspirational showcase** | Just beautiful room, no caption |
| **Question hook** | "What if AI could redesign your room?" |

### Bước 4 — Sinh Primary text (5 variants)

Mỗi variant match 1 hook pattern bro thấy trong video, follow rules:

- ≤125 ký tự lý tưởng (trước "See more" trên mobile, dù Meta auto-expand)
- Câu đầu = hook (5 từ đầu quyết định CTR)
- **"Free"** xuất hiện rõ (IAA app)
- 1 emoji đầu/cuối, không spam
- CTA cuối: `👇` hoặc `Try it free` hoặc câu hỏi
- Match tone of voice video (provocative ≠ aspirational)

### Bước 5 — Sinh Headlines (5 variants)

- **≤40 ký tự** (hard limit Meta cảnh báo qua 30)
- Action verb đầu: "Redesigns", "Get", "See", "Try"
- Có "Free" hoặc "AI" hoặc benefit cụ thể
- Tránh title case rườm rà nếu hook là casual

### Bước 6 — Output

**Luôn** xuất bằng 3 bảng markdown để dễ paste:

**Table 1 — Primary text:**

| # | Primary text |
|---|---|
| 1 | `<text>` |
| ... | ... |

**Table 2 — Headlines:**

| # | Headline | Length |
|---|---|---|
| 1 | `<text>` | NN |
| ... | ... | ... |

**Table 3 — Pairing (chỉ khi user tắt Dynamic Creative):**

| Video | Primary | Headline |
|---|---|---|
| <filename> | 1 | 1 |
| ... | ... | ... |

Backtick-quote từng text để copy giữ nguyên emoji/space.

Khuyến nghị **Dynamic Creative ON** ở cuối → upload tất cả 5 primary + 5 headline, Meta tự xoáy combo CTR cao.

### Bước 7 — Tổng hợp output cuối

Khi xong Mode B, gộp đủ:
1. Country block (§1 — tách 1-2 cluster phù hợp creative language)
2. Strategy 1 dòng (§2 — ROAS gộp / CBO chia)
3. 3 bảng Primary/Headlines/Pairing (§3.6)
4. Câu hỏi đóng: cần thêm description / variant tiếng khác không?

---

## §4 — Khi nào dùng skill này

- User dán screenshot bulk-add modal / hỏi "tier list cho Meta Ads"
- User nói "tạo ad set mới cho T1" / "T2" / "list nước chạy ads"
- User hỏi nước nào chạy creative ngôn ngữ X
- **User dán Play Store URL + folder video + xin Primary/Headlines/copy/ads** → Mode B
- User hỏi "ROAS có cần chia tier không"
- User nói "làm ads cho app này" / "lập campaign Meta cho app"

## §5 — Khi nào KHÔNG dùng

- App là IAP-monetized (in-app purchase) — tiering khác (T1 hẹp hơn, KR/JP/SG quan trọng hơn).
- App là gaming — lookup khác (BR/IN/MX có thể là T2).
- User hỏi tier eCPM cụ thể bằng số $ — nói rõ không có data live, dẫn họ về MMP (AppsFlyer, Adjust).
- User chỉ muốn xem video chứ không cần ads — dùng `classify-translate-videos` thay vì skill này.
