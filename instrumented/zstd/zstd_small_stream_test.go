package zstd

import (
	"bytes"
	"math/rand"
	"testing"
)

func BenchmarkSmallWriteStreamCompression(b *testing.B) {
	randbs := func(n int) []byte {
		bs := make([]byte, n)
		r, err := rand.Read(bs)
		if err != nil {
			b.Fatalf("Failed to generate random bytes for benchmark: %v", err)
		}
		if r < n {
			b.Fatalf("Read %d bytes, less than requested %d", r, n)
		}
		return bs
	}

	count := func(n int) []byte {
		bs := make([]byte, n)
		for i := 0; i < n; i++ {
			bs[i] = byte(n % 255)
		}
		return bs
	}

	for _, tt := range []struct {
		name   string
		rawgen func(n int) []byte
	}{
		{
			name:   "all-zeros",
			rawgen: func(n int) []byte { return make([]byte, n) },
		},
		{
			name:   "count",
			rawgen: count,
		},
		{
			name:   "random",
			rawgen: randbs,
		},
	} {
		b.Run(tt.name, func(b *testing.B) {
			raw := tt.rawgen(b.N)
			var intermediate bytes.Buffer
			w := NewWriter(&intermediate)
			b.SetBytes(1)
			b.ResetTimer()
			for i := 0; i < b.N; i++ {
				_, err := w.Write(raw[i : i+1])
				if err != nil {
					b.Fatalf("Failed writing to compress object: %s", err)
				}
			}
			w.Close()
			b.ReportMetric(float64(intermediate.Len())/float64(b.N), "compressed_bytes/op")
		})

	}
}

func TestSmallWriteStreaming(t *testing.T) {
	b := &bytes.Buffer{}
	for i := 0; i < 5000; i++ {
		b.Write([]byte("Hello World! "))
	}
	data1 := b.Bytes()

	// Compress 1
	buffer1 := &bytes.Buffer{}
	w1 := NewWriterLevel(buffer1, BestSpeed)
	_, err := w1.Write(data1)
	if err != nil {
		t.Fatalf("Failed to write data: %v", err)
	}
	err = w1.Close()
	if err != nil {
		t.Fatalf("Failed to close writer: %v", err)
	}

	// Compress 2
	buffer2 := &bytes.Buffer{}
	w2 := NewWriterLevel(buffer2, BestSpeed)
	for i := 0; i < 5000; i++ {
		_, err := w2.Write([]byte("Hello World! "))
		if err != nil {
			t.Fatalf("Failed to write data: %v", err)
		}
	}
	err = w2.Close()
	if err != nil {
		t.Fatalf("Failed to close writer: %v", err)
	}

	if buffer1.Len() != buffer2.Len() {
		t.Errorf("expected unbuffered writes to use same amount of space as buffered writes.\n")
	}
}
