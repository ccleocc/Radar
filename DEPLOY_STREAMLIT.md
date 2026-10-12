# 部署到 Streamlit Community Cloud（V2.3.2）

## 一、先解决 `ModuleNotFoundError: No module named radar`

这个错误通常表示 GitHub 仓库中只有 `app.py`，或者 `radar/` 被放进了多一层的项目文件夹，导致 Streamlit Cloud 从仓库根目录运行时找不到 `radar/analyzer.py`。当前版本增加了路径探测和友好错误提示，但部署时仍必须上传整个 `radar/` Python 包。

## 二、GitHub 仓库根目录必须长这样

将本 ZIP **解压后，把里面的文件和文件夹直接提交到 GitHub 仓库根目录**。不要把 ZIP 文件本身上传，也不要把整个项目文件夹套在仓库根目录内。

```text
commercial-radar/                 # GitHub 仓库根目录
├── app.py                         # Streamlit 主入口
├── requirements.txt               # 云端依赖
├── .gitignore
├── .streamlit/
│   └── config.toml
├── radar/                         # 必须完整提交
│   ├── __init__.py
│   ├── analyzer.py
│   ├── background.py
│   ├── cninfo.py
│   ├── config.py
│   ├── db.py
│   ├── pipeline.py
│   └── settings.py
├── samples/
├── tests/
└── run_daily.py
```

确认 GitHub 网页上可以直接打开 `radar/analyzer.py`。如果它的实际路径是 `a_share_sea_factory_radar_v2_3/radar/analyzer.py`，就说明多套了一层文件夹；请把内层文件夹中的内容移动到仓库根目录。

## 三、在 Streamlit Cloud 创建应用

1. 登录 Streamlit Community Cloud，选择 **Create app**。
2. 选择对应 GitHub repository 和 branch。
3. **Main file path** 填写 `app.py`。
4. Advanced settings 中选择 Python **3.12**（与当前 Community Cloud 默认版本保持一致）。
5. 点击 Deploy。Cloud 会读取根目录下的 `requirements.txt` 自动安装依赖。

## 四、公开部署前设置管理员密码（强烈建议）

该应用有启动采集、修改关键词、导入公告 PDF、重新分析历史记录和删除商机档案等写操作。设置管理员密码后，公众仍可浏览和导出数据，但这些管理操作只有登录管理员后才可使用。

在 Streamlit Cloud 应用的 **App settings → Secrets** 中添加 TOML：

```toml
RADAR_ADMIN_PASSWORD = "请替换为足够长且唯一的密码"
```

保存后重启应用。侧栏会显示“管理员登录”。不要把真实密码写进 `app.py`、GitHub 文件或公开 issue。此项目的 `.gitignore` 已排除本地 `.streamlit/secrets.toml`。

如果未配置该密码，为保持本地开发习惯，管理功能仍会开放，并显示警告；正式公开部署前请务必设置密码。

## 五、数据持久化说明

当前程序使用 `data/radar.db` SQLite 数据库；`data/` 被 `.gitignore` 排除，部署新实例时会创建空数据库。Streamlit Community Cloud 的本地文件系统不应被视为生产级永久存储；重新部署、实例重建或休眠恢复后，不能依赖 SQLite 文件一定长期保留。

- 小规模验证：可先使用当前 SQLite 方案。
- 面向公众长期使用：建议将公告和商机库迁移到托管 PostgreSQL（例如 Supabase/Neon），并设置定期备份。
- 不要把含有内部备注或不适合公开展示的数据放在公开应用数据库中。应用当前面向公开上市公司公告，但商机评级属于内部分析意见，公开内容前应自行确认披露范围。

## 六、公司名称和标题的 HTML 清洗

新版在巨潮候选解析、数据库写入及历史记录读取时统一清除 HTML 标签（包括 `<em>`），并解码 HTML 实体。因此即使旧数据库保存过 `<em>` 高亮，公司名称、公告标题、地点和项目属性在界面和导出中也会被清理。原始 JSON 中用于展示的公司名/标题字段同样会清理；PDF 正文原文不做全局标签替换，以避免误改真实文本。

## 七、部署问题排查

- `No module named radar`：检查 GitHub 仓库根目录是否存在 `radar/__init__.py` 和 `radar/analyzer.py`；不要只提交 `app.py`。
- `No module named pandas/requests/pypdf/openpyxl`：确认根目录存在 `requirements.txt`，提交后等待云端重新构建。
- 应用能启动但无历史数据：这是新云实例的空 SQLite 数据库所致，不是采集失败。登录管理员后选择日期范围、市场和关键词启动采集。
- 公司名称含 `<em>`：正常情况下新版本会在保存与读取时清洗；若仍出现，请发出现在界面中的属性名、公司和公告 ID，便于定位是否来自 PDF 解析或未清洗的自定义属性。

## 侧栏公司 Logo

V2.3.2 会在左侧导航上方加载中国建筑第八工程局有限公司 Logo。图像使用用户提供的公开网址，无需额外上传图片文件；若网站禁用外链，请将图片保存到仓库 `assets/` 并把 `app.py` 中的 `CSCEC_LOGO_URL` 改为本地图片路径。
