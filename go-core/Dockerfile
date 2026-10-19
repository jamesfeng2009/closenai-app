# Go Core 对外 HTTP 服务镜像。多阶段：编译出静态二进制，运行层只带 CA 与二进制。
# 构建上下文是 go-core/ 本身：docker build -f go-core/Dockerfile go-core
#
# 2026-10-13 本机实测：docker.io 与 gcr.io 直连均为 Bad Gateway，只有
# docker.m.daocloud.io 可达。基础镜像因此做成 build-arg——默认写上游保持可移植，
# 本机/国内 CI 用
#   --build-arg GO_IMAGE=docker.m.daocloud.io/library/golang:1.27-alpine
# 覆盖。运行层不用 gcr.io/distroless（拉不到），改用 alpine + ca-certificates：
# 生产 DATABASE_URL 走 TLS，裸 alpine 没有 CA bundle 会连不上 Supabase。
#
# 2026-10-18 本机实测：proxy.golang.org 在容器内拉 module 中途 EOF（同一命令本机
# 直连也断），与 docker.io 那类问题同源 ⇒ GOPROXY 同样做成 build-arg：默认写上游
# 保持可移植，本机/国内 CI 用 --build-arg GOPROXY=https://goproxy.cn,direct 覆盖。
ARG GO_IMAGE=golang:1.27-alpine
ARG GOPROXY=https://proxy.golang.org,direct
FROM ${GO_IMAGE} AS build
# 全局 ARG 不进 stage 作用域，须在这里重新声明才能被 ENV 引用。
ARG GOPROXY
WORKDIR /src
COPY go.mod go.sum ./
ENV GOPROXY=${GOPROXY}
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /go-api ./cmd/go-api

FROM alpine:3.20
RUN apk add --no-cache ca-certificates
COPY --from=build /go-api /go-api
ENV HTTP_ADDR=:8000
EXPOSE 8000
# 数字 uid，不依赖镜像里存在 nonroot 用户；k8s 侧 runAsNonRoot 因此成立。
USER 65532:65532
ENTRYPOINT ["/go-api"]
