# 把旧 Studio 的先导片接进制片工厂

`film.py import-studio` 读取选定项目，将剧本文字、图片及中文说明冻结到该影片自己的素材目录，可以直接送入已有制作队列。同一套配置和控制台可同时运行多个 Studio 项目，各项目拥有自己的导演选材范围。原片库 C 编号按工作站手册中的 `import-clips` 接入。

## 选择项目与映射

在工作站上找到 `proj_3617a34c` 对应的实际项目 JSON，再查看字段。本机未取得旧项目的原始 schema，命令中的目录和文件名需替换成工作站实际路径。

```powershell
$py = 'D:\ai\venv\Scripts\python.exe'
$project = 'D:\Studio\projects\proj_3617a34c.json'
& $py film.py import-studio $project --inspect
```

输出列出字段路径、类型和长度，每个数组展示第一条记录的结构。例如 `/assets/0/path` 表示第一个素材对象的本地路径。默认可直接读取如下形状。

```json
{
  "id": "proj_3617a34c",
  "name": "先导片",
  "script": "原始剧本全文",
  "seconds": 60,
  "requirements": "保留原文台词与定版，隧道转杭州允许爆闪。",
  "assets": [
    {"id": "img-03", "path": "images/杭州夜研.png", "description": "夜班测试工位，台灯在画面右侧"}
  ]
}
```

| 输入 | 支持形式 |
|---|---|
| 剧本 | 项目内 `script` 或 `script_text` 字符串；也可显式指定 UTF-8 TXT/Markdown 文件。保留原文、行换行和段落。 |
| 素材 | `assets` 数组中的本地路径字符串，或含 `path` / `file_path` / `file` 的对象。支持真实 PNG、JPEG，导入前验证图像格式。 |
| 中文说明 | 优先使用素材行的 `caption` / `description`。缺少时读取映射的 captions_file（依次查 ID、原路径、文件名），再尝试素材旁的同名 .txt。 |
| 计划与历史 | 可保留指定计划 JSON 原样副本 `source-plan.json`，便于查旧镜号、被否候选和修改意见。制作状态与生成队列在新工厂独立建立。 |
| 镜头引用 | 显式 `links` 可锁定原镜号的首图、尾图、上一镜或 C 编号尾帧，绑定在拆镜后、剧情核对前生效。不可定位的镜号会报告错误。 |

若文件形状不同，建立 `studio-map.json`。下面示例表示项目存于 `projects` 数组内、剧本是第一份文稿正文、图片元数据另有命名。只保留自己实际需要的字段。JSON Pointer 的 `~0` 表示键名中的 `~`，`~1` 表示键名中的 `/`。

```json
{
  "project_pointer": "/projects/0",
  "fields": {
    "title": "/name",
    "id": "/id",
    "script": "/scripts/0/content",
    "assets": "/materials"
  },
  "asset_fields": {"id": "/assetId", "path": "/localFile", "caption": "/description"},
  "seconds": 60,
  "requirements": "按原 60 秒剧本制作，杭州办公室接在隧道后。",
  "mode": "auto",
  "music": true,
  "plan_file": "plans/plan_6743a92b.json",
  "links": [
    {"shot": "S02", "first": "C001"},
    {"shot": "S03", "first": "C006", "last": "杭州夜研.png"}
  ]
}
```

- `fields` 中的路径相对于 `project_pointer` 选中的项目，`asset_fields` 的路径相对于每条素材记录。若同名素材来自不同目录，请映射 `asset_fields.name` 到一列唯一的文件名，包含扩展名。
- 剧本单独存放时用 `"script_file":"scripts/先导片.md"`。中文说明单独存放时用 `"captions_file":"asset_captions.json"`，文件可形如 `{"img-03":"杭州夜班实拍"}`；此时移除 `asset_fields.caption`。脚本、说明、计划三个显式文件路径以映射文件所在目录为基准，也可填绝对路径。
- 素材的相对路径从项目 JSON 所在目录以及各 `--root` 目录查找。旧项目在 C 盘、图片在 D 盘时，把实际图片根目录通过 `--root` 传入。若多个根中存在不同的同路径文件，请改用绝对路径消除歧义。
- `first` / `last` 使用导入后的精确图片文件名，或保留的片库编号。仅 `first` 可为 `previous`，表示沿用**新制作中已采用的上一镜尾帧**。如分镜数量或衔接意图变化，应同步调整旧镜号映射。
- 生成模式默认 `review`。需要无人值守的项目请在映射中设 `"mode":"auto"`。时长可以使用 `--seconds 60` 覆盖。

## 先准备，再入队

```powershell
& $py film.py import-studio $project --mapping D:\Studio\studio-map.json --root D:\ai\studio-assets
& $py film.py import-studio $project --mapping D:\Studio\studio-map.json --root D:\ai\studio-assets --enqueue
```

第一条命令返回项目号、快照目录、素材数、有中文说明的素材数、映射条数和目标时长。请按先导片实际情况核对 22 张素材及说明。若剧本或镜头映射引用了 C001/C006 等旧片号，应先用 `import-clips` 保留对应的真实文件和历史最大编号。第二条成功后会返回 job_id，可在主控台选择该任务，或打开 `http://127.0.0.1:8766/monitor?job=任务ID`。

快照位于 `data/studio-imports/<内容摘要>/`。原文、描述、内容、引用和目标规格相同的重复提交复用同一任务。修改剧本、素材、制作要求、模式、目标时长或计划副本会产生新的导入版本。快照中包含 `brief.json`、`manifest.json`、`assets/` 以及可选的 `source-plan.json`。备份或迁移时应包含整个目录。旧 Studio 上被拒绝的 S03 和另选的 S13/S14 仍以历史记录身份保存，是否在新制作中采用由新工厂自己的验收结果决定。
