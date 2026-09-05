package messaging

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/nats-io/nats.go"
)

type JetStream struct {
	connection *nats.Conn
	context    nats.JetStreamContext
}

func Connect(url, clientName string) (*JetStream, error) {
	connection, err := nats.Connect(
		url,
		nats.Name(clientName),
		nats.Timeout(5*time.Second),
		nats.MaxReconnects(-1),
		nats.ReconnectWait(time.Second),
	)
	if err != nil {
		return nil, fmt.Errorf("connect NATS: %w", err)
	}
	jetstream, err := connection.JetStream(nats.PublishAsyncMaxPending(256))
	if err != nil {
		connection.Close()
		return nil, fmt.Errorf("open JetStream context: %w", err)
	}
	return &JetStream{connection: connection, context: jetstream}, nil
}

func (stream *JetStream) Close() {
	if stream == nil || stream.connection == nil {
		return
	}
	_ = stream.connection.Drain()
	stream.connection.Close()
}

func (stream *JetStream) Healthy() bool {
	return stream != nil && stream.connection != nil && stream.connection.IsConnected()
}

func (stream *JetStream) Ensure(ctx context.Context) error {
	configuration := jobStreamConfiguration()
	if _, err := stream.context.StreamInfo(orchestration.JobStreamName, nats.Context(ctx)); err != nil {
		if !errors.Is(err, nats.ErrStreamNotFound) {
			return fmt.Errorf("inspect job stream: %w", err)
		}
		if _, err := stream.context.AddStream(configuration, nats.Context(ctx)); err != nil {
			return fmt.Errorf("create job stream: %w", err)
		}
		return nil
	}
	if _, err := stream.context.UpdateStream(configuration, nats.Context(ctx)); err != nil {
		return fmt.Errorf("update job stream: %w", err)
	}
	return nil
}

func jobStreamConfiguration() *nats.StreamConfig {
	return &nats.StreamConfig{
		Name:       orchestration.JobStreamName,
		Subjects:   []string{"rainpulse.jobs.>", orchestration.ProductPublishedSubject},
		Storage:    nats.FileStorage,
		Retention:  nats.LimitsPolicy,
		Discard:    nats.DiscardOld,
		MaxAge:     7 * 24 * time.Hour,
		MaxBytes:   1024 * 1024 * 1024,
		Duplicates: 10 * time.Minute,
	}
}

func (stream *JetStream) Publish(ctx context.Context, event workflow.OutboxEvent) error {
	message := nats.NewMsg(event.Subject)
	message.Data = event.Payload
	message.Header.Set(nats.MsgIdHdr, event.ID.String())
	if _, err := stream.context.PublishMsg(message, nats.Context(ctx)); err != nil {
		return fmt.Errorf("publish %s: %w", event.Subject, err)
	}
	return nil
}

func (stream *JetStream) ConsumeResults(ctx context.Context, handler func(context.Context, []byte) (bool, error)) error {
	subscription, err := stream.context.PullSubscribe(
		orchestration.JobResultsSubject,
		orchestration.ResultConsumerName,
		nats.BindStream(orchestration.JobStreamName),
		nats.ManualAck(),
		nats.AckExplicit(),
		nats.MaxDeliver(10),
		nats.AckWait(30*time.Second),
	)
	if err != nil {
		return fmt.Errorf("subscribe result consumer: %w", err)
	}
	defer subscription.Unsubscribe() //nolint:errcheck

	for {
		if err := ctx.Err(); err != nil {
			return nil
		}
		messages, err := subscription.Fetch(1, nats.MaxWait(time.Second))
		if errors.Is(err, nats.ErrTimeout) {
			continue
		}
		if err != nil {
			return fmt.Errorf("fetch result event: %w", err)
		}
		for _, message := range messages {
			applied, handleErr := handler(ctx, message.Data)
			if handleErr != nil {
				slog.Warn(
					"RainPulse result event rejected",
					"subject", message.Subject,
					"error", handleErr,
				)
			} else if !applied {
				slog.Info(
					"RainPulse result event already recorded",
					"subject", message.Subject,
				)
			}
			switch {
			case handleErr == nil:
				if err := message.Ack(); err != nil {
					return fmt.Errorf("ack result event: %w", err)
				}
			case errors.Is(handleErr, orchestration.ErrInvalidEvent):
				if err := message.Term(); err != nil {
					return fmt.Errorf("terminate invalid result event: %w", err)
				}
			default:
				if err := message.NakWithDelay(2 * time.Second); err != nil {
					return fmt.Errorf("nak result event: %w", err)
				}
			}
		}
	}
}
