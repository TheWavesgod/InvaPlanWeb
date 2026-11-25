# Render 部署 / Hosted review

部署的是 39 个物种、62 座城市的完整研究版。Render 从 GitHub 构建应用，运行数据单独存放在你的 Render 持久磁盘。网站默认通过 HTTPS 使用浏览器自带的登录框。账号为 `reviewer`，密码由 Render 生成。

## 首次上线

1. 用你的 Render 账号打开[本分支的部署页面](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2FTheWavesgod%2FInvaPlanWeb%2Ftree%2Fbinyue%2Fpublic-demo)。如需连接 GitHub，选择 `TheWavesgod/InvaPlanWeb`。检查分支为 `binyue/public-demo`，配置文件为根目录的 `render.yaml`。
2. 在费用预览确认后创建服务。配置使用 Frankfurt 的一台 1 CPU、2 GB 内存 Web Service 和 1 GB 持久磁盘。首次空盘启动时，`/healthz` 返回 204；登录后的首页暂时返回 503，等待首次数据传输。
3. 用 Render 账号的 SSH 公钥通过 SFTP 连接这台服务。SSH 主机和服务用户名在 **Connect → SSH**，文件传输使用这个账号。打开磁盘目录 `/var/data`，按下表上传。先传基础文件和城市包，最后传城市索引。查看本机 `area-cities.json` 的 `cities[].file` 可核对当前使用的子目录和文件名。[Render SSH](https://render.com/docs/ssh) · [磁盘和文件传输](https://render.com/docs/disks#transferring-files)

   | 本地文件 | Render 目的地 |
   |---|---|
   | 研究工作区 `data/jiaxing-39/` 下的 `manifest.json`、`values.npz`、`provenance.json`、`maxsss.json`、`climate.npz` | `/var/data/release/` |
   | `%LOCALAPPDATA%/InvaPlan/area-cities/` 下由索引引用的 62 个 `area-*.npz` | `/var/data/area-cities/` 下保持索引中的相对路径，包括 `bundles-…/` 子目录 |
   | 同一目录的 `area-cities.json` | `/var/data/area-cities/area-cities.json` |

4. 在 Render **Manual Deploy → Restart service** 重启。用无痕窗口打开服务的 HTTPS 网址，输入 `reviewer` 和 Render **Environment → INVAPLAN_REVIEW_PASSWORD** 中的密码。打开「等面积选址比较」，确认汇总为 62 城并可进入逐物种地图，再把网址和评审凭据交给获准访问的人。受邀者可以查看并导出研究结果。

## 文章发表后

在该服务的 **Environment** 添加 `INVAPLAN_ACCESS_MODE=public`，保存并重新部署。用新的无痕窗口打开原网址，检查地图和详情可以直接访问。恢复登录时把同一个变量改为 `private` 并重新部署。修改 `INVAPLAN_REVIEW_PASSWORD` 并重新部署即可轮换共享密码。数据目录与网址均可沿用。

## English

The Blueprint deploys the full 39-species, 62-city application in Frankfurt with a paid 2 GB web instance and a 1 GB persistent disk. Review the costs in your Render workspace before applying. Deploy the `binyue/public-demo` branch using the link above. The service starts on an empty disk so that you can connect over SFTP. Upload the five release files and the indexed city bundles to the paths in the table, transfer the city index last, then restart the service.

Visit the Render HTTPS URL in a fresh private window. Sign in as `reviewer` with the generated `INVAPLAN_REVIEW_PASSWORD` from the service environment. Verify the 62-city map and a species detail view. The login covers the app, its API, map layers and the data used for exports. The anonymous `/healthz` probe returns an empty response and only indicates process availability.

After publication, set `INVAPLAN_ACCESS_MODE=public` in the service environment and redeploy. The same URL then allows anonymous access. To require sign-in again, set the value to `private` and redeploy. Share reviewer credentials privately.
