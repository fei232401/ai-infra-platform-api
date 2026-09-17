# platform CI —— ai-infra-platform-api 的镜像构建流水线

这份流水线是 `sre-lab-ci` 的**第二个实例**。两个 job 长得像但有三处关键差异，
差异本身就是这个项目最值得讲的部分（见文末「与 sre-lab-ci 的差异」）。

## 它做什么

```
Gitea push (ai-infra-platform-api)
        │  webhook: http://k3d-ai-cluster-server-0:30080/generic-webhook-trigger/invoke?token=***
        v
Jenkins job `platform-ci`  （SCM: gitea:3000/fei232401/ai-infra-platform-api.git，分支 */main）
        │
        ├─ 1. Checkout        从私有仓检出代码（走 Jenkins 凭据 gitea-scm）
        ├─ 2. Trigger Guard   只看本次推送碰了没碰 src/ tests/ ci/ Dockerfile requirements* pytest.ini
        │                     没碰 → SHOULD_BUILD=false，后面所有重量级 stage 全部跳过
        ├─ 3. Resolve Tag     IMAGE_REF = k3d-sre-registry:5000/platform/ai-infra-platform-api:<short sha>
        ├─ 4. Fetch Schema    装 netrc → clone 私有仓 ai-infra-platform-db 拿 schema 契约
        ├─ 5. Test            pip install -r requirements-dev.txt && pytest（82 个用例）
        │                     TEST_DATABASE_DSN → postgres.ai-platform.svc.cluster.local/ai_infra_test
        ├─ 6. Migrate          用 db 仓的 migrations/001_init.sql 应用到交付库 ai_infra（幂等）
        ├─ 7. Build           buildah bud（vfs + chroot，跑在 k8s Pod 里，不用 docker daemon）
        ├─ 8. Push            buildah push → k3d-sre-registry:5000
        ├─ 9. Verify In Registry  查 registry 的 tags/list 里确实有这颗 sha
        └─10. Update GitOps   sed 改 sre-lab-gitops/production/apps/ai-platform/api.yaml 的 image tag
                              → push 回 sre-lab 仓 → ArgoCD 3 分钟内收敛 → 滚动更新
```

## 阶段顺序不是随意的

**Test → Migrate → Build → 写回 GitOps**，这个顺序有约束：

- `Migrate` 放在构建**之前**：迁移失败就不该让新镜像上线。反过来说，
  只要流水线绿了，就说明「代码 + 表结构」这一对是一致的。
- `Update GitOps` 放在最后：它是**唯一的对外副作用**。前面任何一步红了，
  ArgoCD 都不会看到新 tag，集群保持在上一个可用版本。
- `Verify In Registry` 卡在写回之前：确认镜像真的在仓库里了，再改 GitOps。
  否则 ArgoCD 会同步到一个拉不到的 tag，Pod 卡在 ImagePullBackOff。

## 为什么 schema 迁移放在 CI 里

`ai-infra-platform-db` 是 schema 的唯一契约来源。让流水线在部署前把这份契约应用到目标库，
「代码」和「表结构」才在同一根时间线上。人手跑迁移迟早会错位 —— 而且是那种
「上周五改了字段、这周三才发现服务起不来」的错位。

`ci/migrate.py` 是幂等的：`schema_migration` 表已存在就跳过（约定是迁移只增不改）。

## 与 sre-lab-ci 的差异（这三条是踩出来的）

| 差异 | sre-lab-ci | platform-ci | 为什么 |
|---|---|---|---|
| SCM 凭据 | **没有** `credentialsId` | `credentialsId: gitea-scm` | sre-lab 是公开仓，匿名就能读；platform 的 api/db 是私有仓。私有仓的 SCM 检出必须挂凭据，而凭据要存成 Jenkins 的 `Username with password`（明文塞进 XML 会被当成密文，取出来是乱码）—— 所以用脚本台建 |
| GitOps 写回路径 | `FILE=${GITOPS_FILE}`（相对工作区） | clone `.gitops` 后 `FILE=${GITOPS_FILE}` | sre-lab 的 job 工作区**就是** sre-lab 仓本身，GitOps 目录在里面；platform 的 job 检出的是 api 仓，GitOps 清单不在工作区里，必须先 clone 出来。照抄 `../` 前缀会让 sed 打在不存在的文件上 |
| Fetch Schema 前的 netrc | 不需要 | 必须装 | db 仓是私有的，不装 netrc 时 git clone 会被当成匿名请求打到 **404**，报错长得很像「仓库不存在」，容易往错方向查 |
| 首次落地 | —— | 清单不存在时**跳过写回**而不是报错 | 让引导顺序可以是「先跑通镜像，再落 GitOps 清单」，避免 ArgoCD 先同步到不存在的 tag |

## 需要什么才能复现

| 类型 | 名称 | 用途 |
|---|---|---|
| Jenkins 凭据 | `gitea-scm`（Username with password） | 私有仓 SCM 检出 |
| Jenkins 凭据 | `platform-webhook-token`（Secret text） | GenericTrigger 的 token |
| K8s Secret | `jenkins/gitea-netrc`（key: `netrc`） | Pod 内 clone/push 私有仓与 GitOps 仓 |
| K8s ConfigMap | `jenkins/buildah-registries` | buildah 走 daocloud 镜像源 |
| K8s PVC | `jenkins/buildah-storage-platform` | buildah 的 vfs 存储 |
| Jenkins 插件 | generic-webhook-trigger 2.4.3 / kubernetes 4547 / git 5.10.1 | —— |

## 加一个新服务要改什么

1. 复制这份 Jenkinsfile，改 `IMAGE_NAME` / `SERVICE_NAME` / `PIPELINE_TITLE` / `GITOPS_FILE`
2. Jenkins 建 job（SCM 指向新仓、挂 `gitea-scm`、GenericTrigger 用新 token）
3. 新仓挂 webhook，token 用第 2 步那个
4. GitOps 仓里建好被 sed 的那份清单（`image:` 那一行必须先存在）

第 4 步是最容易漏的：`sed` 打在不存在的文件上，`set -e` 会直接让流水线红。
所以本流水线加了「文件不存在就跳过写回」的守卫。
