// 真连接面（R4-④）。
//
// 拓扑声明与发布在 topology.go／publisher.go 里已经可测（fake Declarer／fake Confirmer），
// 本文件只负责把 amqp091 的对象**收进一个生命周期**：拨号 → 一条 confirm 通道 → 声明 → 发布 →
// 先关通道再关连接（17 §11 的第 4/5 步顺序是有意义的：连接先关会让通道上的在途确认变成
// 一个「connection closed」而不是 broker 的真实答复）。
//
// 进程接线（`internal/messaging/dispatch`）只认 Session 这四件事，
// 所以 worker 的启停/重连逻辑不需要真 broker 也能测。
package amqp

import (
	"context"
	"errors"
	"fmt"
	"net"
	"strings"
	"time"

	amqp091 "github.com/rabbitmq/amqp091-go"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/outbox"
)

// Session 是一条连接＋一条已进 confirm 模式的通道。
type Session struct {
	conn *amqp091.Connection
	ch   *amqp091.Channel
}

// Dial 建立会话。
//
// ⚠️ amqp091-go v1.10.0 **没有** DialContext（实测 `grep '^func Dial' connection.go` 只有
// Dial／DialTLS／DialTLS_ExternalAuth），而 `Dial` 走的是没有超时的 net.Dial：broker 域名
// 黑洞时它会一直挂着。进程接线要求「关掉 kill switch 时启动不受影响、开着时拨号能超时」，
// 所以这里自己带一个 dialer 超时，并把握手放进可取消的等待里。
// 超时/取消后迟到的那条连接会被就地关掉——不关掉它就是一条没人 Close 的 TCP 连接。
func Dial(ctx context.Context, url string, dialTimeout time.Duration) (*Session, error) {
	if dialTimeout <= 0 {
		return nil, fmt.Errorf("amqp: dialTimeout=%v 非法（必须为正，否则拨号没有上限）", dialTimeout)
	}
	type dialed struct {
		sess *Session
		err  error
	}
	done := make(chan dialed, 1)
	go func() {
		d := net.Dialer{Timeout: dialTimeout}
		conn, err := amqp091.DialConfig(url, amqp091.Config{
			// 没有这个方法值语法（Go 不支持偏应用），所以包一层闭包把 ctx 传进去。
			Dial:      func(network, addr string) (net.Conn, error) { return d.DialContext(ctx, network, addr) },
			Heartbeat: 30 * time.Second, // 0＝关掉心跳，broker 静默死亡时客户端不会自己发现
		})
		if err != nil {
			done <- dialed{err: fmt.Errorf("amqp: dial %s: %w", redactURL(url), err)}
			return
		}
		ch, err := conn.Channel()
		if err != nil {
			_ = conn.Close()
			done <- dialed{err: fmt.Errorf("amqp: open channel: %w", err)}
			return
		}
		// 14 §11 的前提：发布者必须在 confirm 模式，否则 DeferredConfirmation 永远等不到答复。
		if err := ch.Confirm(false); err != nil {
			_ = ch.Close()
			_ = conn.Close()
			done <- dialed{err: fmt.Errorf("amqp: confirm mode: %w", err)}
			return
		}
		done <- dialed{sess: &Session{conn: conn, ch: ch}}
	}()

	select {
	case res := <-done:
		return res.sess, res.err
	case <-ctx.Done():
		// 让 goroutine 有机会把迟到的连接关掉，而不是留一条孤儿连接。
		go func() {
			if res := <-done; res.sess != nil {
				_ = res.sess.Close()
			}
		}()
		return nil, fmt.Errorf("amqp: dial %s: %w", redactURL(url), ctx.Err())
	}
}

// Declare 在本会话上重放整套拓扑（幂等，见 topology.go 的 Declare）。
func (s *Session) Declare() error { return Declare(s.ch) }

// Publisher 给这条通道配一个发布器（confirm 已开，无需再 Open）。
func (s *Session) Publisher(confirmTimeout time.Duration) (outbox.Publisher, error) {
	pub, err := NewPublisher(&ConfirmChannel{Ch: s.ch}, Options{ConfirmTimeout: confirmTimeout})
	if err != nil {
		return nil, err
	}
	return pub, nil
}

// Channel 回这条会话里**已经在 confirm 模式**的那条通道（T2-05 的巡检工具用）。
//
// 为什么导出这一格而不是让工具自己「Dial 之外再开一条通道」：Dial 上面那三段
// （带超时的拨号 → 打开通道 → 进 confirm 模式）是一个整体，工具里再拼一遍一定会漏掉
// 第三段，而漏掉它的表现最难查——`PublishWithDeferredConfirm` 照样返回成功，
// 只是那条确认永远等不到（14 §11 踩过）。
//
// 它给的是同一条通道，所以调用方**不许**再 `Confirm(false)`（重复进 confirm 模式是
// amqp091 的非法状态），也不许拿去 `Consume`：巡检工具用 `basic.get` 读、用同一通道回投，
// worker 面的形状不受影响。
func (s *Session) Channel() *amqp091.Channel { return s.ch }

// Alive 判连接是否还在。broker 挂了以后 amqp091 不会自动重连（v1.10.0 没有自动恢复），
// 所以进程面每轮认领前问一次，死了就重拨——这是「worker 必须耐受重启」（17 §2 末句）的实现。
func (s *Session) Alive() bool { return s.conn != nil && !s.conn.IsClosed() }

// Close 按 17 §11 的顺序：先通道、再连接，两个错误都不吞。
func (s *Session) Close() error {
	var errs []error
	if s.ch != nil && !s.ch.IsClosed() {
		if err := s.ch.Close(); err != nil {
			errs = append(errs, fmt.Errorf("amqp: close channel: %w", err))
		}
	}
	if s.conn != nil && !s.conn.IsClosed() {
		if err := s.conn.Close(); err != nil {
			errs = append(errs, fmt.Errorf("amqp: close connection: %w", err))
		}
	}
	return errors.Join(errs...)
}

// redactURL 把 amqp://user:pass@host/ 里的口令换成 ***。
// 日志里出现明文口令就是凭据外泄，而拨号失败恰恰是最需要记这条日志的时刻
// （amqp091-go v1.10.0 没有 URL 解析器可借，实测 `grep 'func Parse' *.go` 为空，所以自己做）。
func redactURL(raw string) string {
	at := strings.LastIndex(raw, "@")
	scheme := strings.Index(raw, "://")
	if at <= 0 || scheme < 0 || scheme >= at {
		return raw // 没有 user:pass@ 这一段，原样回（不回 "***"，那会把主机名也抹掉）
	}
	return raw[:scheme+3] + "***" + raw[at:]
}
