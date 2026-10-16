//go:build tools

// 这个文件**永不参与正常构建**（build tag `tools`），存在只有一个理由：
// 让 `go mod tidy` 不因为「没人 import 这两个插件」而把它们从 go.mod 里删掉。
// 用法（scripts/gen_proto.py 走的就是这两条）：
//
//	cd go-core/tools/protogen
//	go build -o "$BIN/protoc-gen-go" google.golang.org/protobuf/cmd/protoc-gen-go
//	go build -o "$BIN/protoc-gen-go-grpc" google.golang.org/grpc/cmd/protoc-gen-go-grpc
package protogen

import (
	_ "google.golang.org/grpc/cmd/protoc-gen-go-grpc"
	_ "google.golang.org/protobuf/cmd/protoc-gen-go"
)
