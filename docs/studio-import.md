# 导入一份已有的剧本与参考资产

先本地运行 `python film.py import-studio PROJECT_JSON --inspect` 查看字段，再用映射冻结需要的素材。举例，一份虚构温室短片可使用 `project_id`、`script`、`assets` 三个字段；每张图包含相对路径和说明。文件不在项目目录时，只用 `--root` 指定已经获准的根目录。

~~~json
{
  "fields": {"id": "/project_id", "script": "/script", "assets": "/assets"},
  "asset_fields": {"path": "/path", "caption": "/description"},
  "seconds": 6,
  "mode": "auto",
  "links": [{"shot": 1, "first": "greenhouse.png"}]
}
~~~

`python film.py import-studio PROJECT_JSON --mapping MAP_JSON` 先准备项目；添加 `--enqueue` 后提交。原剪辑计划可用 `plan_file` 留存。不要将被放弃条次视作采用片。

按实际资产索引创建历史片号清单，包含 `last_issued` 与 `clips`（编号到原视频路径的映射）。计数包含已删条次。用 `python film.py import-clips MANIFEST` 注册后，镜头可用片号引用已核实的历史尾帧。项目按显式映射与同名说明冻结输入。
