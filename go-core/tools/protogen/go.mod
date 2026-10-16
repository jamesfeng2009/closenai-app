// Module protogen 只做一件事：**把 codegen 插件的版本钉在仓里**，
// 好让 scripts/gen_proto.py 在任何一台机器上生成出同一份字节。
//
// 为什么是一个**独立 module**（go-core 里唯一的 nested module）：
// protoc-gen-go 与 protoc-gen-go-grpc 都是 `package main`，谁也不 import 它们。
// 若把它们写进 ../go.mod，`go mod tidy` 会因为「无人 import」直接删掉 ⇒ 版本钉不住；
// 而把它们留在 go-core 的生产依赖面里，又会把两个纯构建期模块算进 R2 要数的
// 「go list -m all」计数（tasks.md §R2 逐字要求依赖新增走 D-7 式取证）。
// 拆开来 ⇒ 生产 module 只多「生成的 pb 代码真正 import 的」protobuf/grpc 两条，
// 构建期版本钉在 go.sum 里，两边都能各自实测。
module github.com/jamesfeng2009/closenai-app/go-core/tools/protogen

go 1.27.1

require (
	google.golang.org/grpc/cmd/protoc-gen-go-grpc v1.5.1
	google.golang.org/protobuf v1.36.12
)
