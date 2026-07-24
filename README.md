# 自动下单机器人

这是一个 CSV 驱动的自动下单机器人。程序会读取订单表格，按 `run_at` 或随机排期等待，到点后打开产品链接执行下单流程。

现在支持两种运行方式：

- 图形界面：双击启动，选择订单 CSV，填写分配天数，点击开始下单。
- 命令行：适合放服务器或长期无人值守运行。

## 双击运行

### Windows

双击：

```text
start_order_bot_windows.bat
```

首次运行会自动创建 `.venv`，并安装 `Playwright` 和 Chromium 浏览器。如果自动安装失败，再在当前目录手动运行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

### Mac

双击：

```text
start_order_bot_mac.command
```

如果第一次打不开，先在终端执行一次：

```bash
chmod +x start_order_bot_mac.command
```

首次运行会自动创建 `.venv`，并安装 `Playwright` 和 Chromium 浏览器。如果自动安装失败，再在当前目录手动运行：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## 图形界面功能

打开后顶部会显示三个 Tab，切换 Tab 不会中断其他正在执行的任务：

- `去下单`：读取订单数据并自动下单。
- `发邮件`：登录邮箱后批量发送订单确认、物流、VAT 发票或自定义邮件。
- `生成发票`：读取发票数据表，根据 `company_name` 自动生成每家公司一个模板上传入口，支持 PDF/HTML 模板批量替换并导出发票。

下单页可以：

- 选择订单 CSV 文件。
- 输入随机分配天数，例如 `3` 或 `30`。
- 设置随机下单时间段，例如 `09:00` 到 `22:00`。
- 设置支付方式，默认 `bank_transfer`，对应结账页里的 `Bank Transfer`。
- 选择 CSV 后不会自动生成随机排期。
- 点击“预览排期”可手动查看每条订单的计划下单时间。
- 点击“开始下单”后才生成本次随机排期，并显示订单完整表格、秒级计划下单时间、状态、下单倒计时和运行日志。
- 对 CSV 里已有 `run_at` 的订单，优先使用 `run_at`，不再随机分配。
- “按国家时区下单”默认开启：`run_at` 和随机时间段都会按订单 `country` 对应的当地时间解释，再自动换算成实际等待时间。

发票页数据文件字段建议使用小写下划线变量名：

- `company_name`：必填，作为选择发票模板的公司 key。
- `vat_invoice_number`、`payment_date`、`date_of_supply`、`supplier`、`sku`、`description`、`specification`、`quantity`、`unit_price`、`net_amount`、`vat_amount`、`gross_amount`：可按模板需要填写。
- 发票模板支持 PDF、HTML、HTM。HTML 模板使用 `{{变量名}}` 占位，例如 `{{company_name}}`、`{{vat_invoice_number}}`，变量名对应数据文件列名。
- PDF 发票模板优先支持“表单字段名精准填充”：用 Adobe Acrobat、Foxit 或 LibreOffice 给 PDF 添加可填写表单字段，字段名设置为数据列名，例如 `vat_invoice_number` 或 `{{gross_amount}}`。程序会按字段名填充并扁平化输出为普通 PDF。
- 如果 PDF 模板没有表单字段，程序会回退到固定坐标覆盖模式；这种方式依赖版式位置，不如表单字段稳定。
- 导出格式支持 PDF、HTML、PNG、JPG、Word（DOCX）。输出会按公司名称创建文件夹，文件名为“公司名称-发票号”，例如 `TAGVENUE LIMITED/TAGVENUE LIMITED-INV-0001.pdf`。
- 发票预览文件会临时生成在安装目录下的 `invoice_previews`，关闭预览弹窗后会自动清理。

执行模式：

- `browser`：到点后打开浏览器，进入商品页，加入购物车并填写结账信息。
- `dry-run`：只走排期和日志，不打开浏览器，适合测试。

安全开关：

- 默认勾选“自动点击下单”，会在字段校验通过后提交最终订单。
- 如需只填写不提交，请取消勾选“自动点击下单”。
- 默认不勾选“失败时保留浏览器”；勾选后下单失败不会关闭结账页面，方便排查字段或按钮问题。
- 默认不勾选“国家搜不到用网站国家”；CSV 国家在结账页搜索不到时会记录为下单失败。
- 勾选“国家搜不到用网站国家”后，CSV 国家搜索不到会保留网站自动匹配的国家并继续下单。

## CSV 字段

必填字段：

- `order_id`：订单编号
- `run_at`：指定下单时间，可留空。例如 `2026/7/4 16:45`
- `email`：邮箱
- `product_url`：产品链接；同一订单要买多个产品时，用英文逗号分隔多个链接，例如 `"https://example.com/a, https://example.com/b"`，程序会先逐个加入购物车再结账
- `quantity`：数量
- `full_name`：收货人姓名
- `country`：国家或地区
- `address_line`：地址
- `city`：城市
- `postal_code`：邮编
- `payment_method`：支付方式，可留空；默认 `bank_transfer`
- `notes`：备注，可留空

可选字段：

- `phone`：手机号码/联系电话，可留空；填写后会尝试自动填入结账页电话字段
- `timezone`：时区覆盖，可留空；例如 `America/Los_Angeles`，适合美国、加拿大、澳大利亚等多时区国家
- `state`
- `province`
- `address_line2`
- `first_name`
- `last_name`
- `country_code`

## 命令行用法

生成三天内随机排期：

```powershell
python -m order_bot --csv "D:\Tencent\xwechat_files\wxid_3r5n3ilqh0c522_480c\msg\file\2026-07\订单数据(1).csv" --spread-days 3
```

等待时间并执行 dry-run：

```powershell
python -m order_bot --csv "D:\Tencent\xwechat_files\wxid_3r5n3ilqh0c522_480c\msg\file\2026-07\订单数据(1).csv" --spread-days 3 --run
```

等待时间并打开浏览器填写订单：

```powershell
python -m order_bot --csv "D:\Tencent\xwechat_files\wxid_3r5n3ilqh0c522_480c\msg\file\2026-07\订单数据(1).csv" --spread-days 3 --run --mode browser
```

等待时间并真实提交最终订单：

```powershell
python -m order_bot --csv "D:\Tencent\xwechat_files\wxid_3r5n3ilqh0c522_480c\msg\file\2026-07\订单数据(1).csv" --spread-days 3 --run --mode browser --submit-final
```

如果需要失败时保留浏览器窗口，可在命令行追加：

```powershell
--keep-open-on-failure
```

如果需要国家搜索不到时使用网站自动匹配的国家继续下单，可追加：

```powershell
--allow-detected-country-on-mismatch
```

## 日志

- `logs/schedule.csv`：本次生成的排期
- `logs/orders.jsonl`：每次执行结果的审计日志
- 源码脚本运行时，日志、排期和失败截图保存在当前运行目录；Mac 安装包运行时会保存到 `~/Library/Application Support/AutoOrderBot/logs`，避免写入只读系统目录。
- Chromium 浏览器缓存保存在当前用户固定目录，安装新版后会复用，不会每次更新都重新下载。
- 邮箱登录信息保存在当前用户固定目录，不保存在安装目录；更新安装包只覆盖程序文件，不会清除已保存邮箱。只有点击邮件界面里的 `退出登录` 或 `删除下拉邮箱` 才会删除对应邮箱信息。
  - Windows：`%LOCALAPPDATA%\AutoOrderBot\email_accounts.json`
  - macOS：`~/Library/Application Support/AutoOrderBot/email_accounts.json`

## 打包安装程序

应用名称固定为“自动下单机器人”，打包时会自动生成程序图标。

每次打包都会输出到：

```text
build/YYYYMMDD-HHMMSS/Windows
build/YYYYMMDD-HHMMSS/mac
```

注意：Windows 电脑只能生成 Windows 安装包；Mac 电脑只能生成 Mac 安装包。不能在 Windows 下直接生成可安装的 Mac `.pkg/.dmg`，因为 macOS 的 `.app`、`pkgbuild`、`hdiutil` 都必须在 macOS 上运行。

打包依赖通常只需要第一次下载，后续再次执行打包会复用 `.venv` 里已经安装的依赖。安装包不内嵌 Chromium 浏览器，用户首次使用浏览器下单功能时会自动下载到当前用户固定缓存目录，后续更新安装包会继续复用。

注意：如果用户首次运行时 Chromium 下载中途失败，例如网络断开或服务端关闭连接，下一次使用浏览器功能仍会重新下载，直到完整下载成功为止。

Windows 打包：

```powershell
.\build_windows.ps1
```

要求本机安装 Inno Setup 6，用于生成可安装的 `.exe` 安装包。安装包使用固定 `AppId`，再次运行新版安装包会覆盖升级旧版程序。

Windows 安装包不把 Chromium 浏览器本体塞进安装程序，首次使用浏览器下单功能时会自动下载到当前用户固定缓存目录，请保持网络可用；同一用户后续更新安装包会复用已有缓存。

Windows 最终发给别人的是：

```text
build/YYYYMMDD-HHMMSS/Windows/installer/自动下单机器人-安装包-版本号-时间戳.exe
```

如果没有看到 `installer` 目录里的 `.exe`，说明本机没有安装 Inno Setup 6；这时目录里只会有便携版 `.zip`，便携版不是安装包，也不会显示升级安装。

Mac 打包：

```bash
sh build_mac.sh
```

Mac 默认只生成 `.pkg` 安装包，体积更小。安装包使用固定 bundle/package identifier，后续新版安装包会按同一应用升级。

如果确实需要额外生成 `.dmg`，可以手动加参数：

```bash
sh build_mac.sh --mac-dmg
```

Mac 安装包不把 Chromium 浏览器本体塞进 `.app`，首次使用浏览器下单功能时会自动下载到当前用户固定缓存目录，请保持网络可用；同一用户后续更新安装包会复用已有缓存。

Mac 最终发给别人的是：

```text
build/YYYYMMDD-HHMMSS/mac/自动下单机器人-版本号-时间戳.pkg
```

如果选择生成 `.dmg`，同目录会额外出现 `.dmg`，也可以把 `.dmg` 发给别人安装。

### 用 GitHub Actions 打包 Mac 安装包

如果你没有 Mac，可以把代码推送到 GitHub，然后让 GitHub Actions 的 macOS 机器自动打包。

操作步骤：

1. 在 GitHub 新建一个私有仓库或公开仓库。
2. 把本项目代码推送到这个仓库。
3. 打开仓库页面，进入 `Actions`。
4. 左侧选择 `Build Installers`。
5. 点击 `Run workflow`。
6. `target` 选择：
   - `all`：同时打包 Windows 和 Mac。
   - `windows`：只打包 Windows。
   - `mac`：只打包 Mac。
7. `version` 可以留空；如果要升级版本，填例如 `0.1.1`。
8. `build_mac_dmg` 默认不勾选；不勾选时只生成 Mac `.pkg`，勾选后才额外生成 `.dmg`。
9. `publish_release` 默认保持勾选；任务成功后会自动发布到仓库首页右侧的 `Releases` 下载区。
10. 等任务跑完后，可以在本次运行记录底部 `Artifacts` 下载，也可以回到仓库首页右侧 `Releases` 下载。

下载文件说明：

- `自动下单机器人-Windows-安装包-时间戳`：里面是 Windows `.exe` 安装包。
- `自动下单机器人-macOS-安装包-时间戳`：默认里面是 Mac `.pkg`；只有勾选 `build_mac_dmg` 时，`.dmg` 才会一起放进去。

GitHub Actions 已配置缓存：依赖成功下载一次后，后续通常会复用缓存。Windows 和 Mac 安装包都会在用户首次使用浏览器功能时自动下载 Chromium，避免打包阶段处理浏览器深层目录或可执行文件失败。

自动发布的 Release 标签格式为 `installers-YYYYMMDD-HHMMSS`，里面会直接附上 `.exe`、`.pkg`；只有勾选生成 DMG 时才会附上 `.dmg`。

注意：当前 Mac 安装包默认未做 Apple 开发者签名和公证。自己或小范围测试通常可以安装；如果要大量发给陌生用户，建议购买 Apple Developer 账号后再增加签名、公证流程。

如果想让 Windows 和 Mac 的产物放到同一个时间戳目录，可以传入同一个时间戳：

```powershell
.\build_windows.ps1 -Timestamp 20260705-153000
```

```bash
sh build_mac.sh --timestamp 20260705-153000
```
