package zstd

/*
// support decoding of "legacy" zstd payloads from versions [0.4, 0.8], matching the
// default configuration of the zstd command line tool:
// https://github.com/facebook/zstd/blob/dev/programs/README.md
#cgo CFLAGS: -DZSTD_LEGACY_SUPPORT=4 -DZSTD_MULTITHREAD=1

#include "zstd.h"
*/
import "C"
import (
	"bytes"
	prof "cgoprof/runtime_go/cgoprof"
	"errors"
	"io/ioutil"
	"unsafe"
)

// Defines best and standard values for zstd cli
const (
	BestSpeed          = 1
	BestCompression    = 20
	DefaultCompression = 5
)

var (
	// ErrEmptySlice is returned when there is nothing to compress
	ErrEmptySlice = errors.New("Bytes slice is empty")
)

const (
	// decompressSizeBufferLimit is the limit we set on creating a decompression buffer for the Decompress API
	// This is made to prevent DOS from maliciously-created payloads (aka zipbomb).
	// For large payloads with a compression ratio > 10, you can do your own allocation and pass it to the method:
	// dst := make([]byte, 1GB)
	// decompressed, err := zstd.Decompress(dst, src)
	decompressSizeBufferLimit = 1000 * 1000

	zstdFrameHeaderSizeMin = 2 // From zstd.h. Since it's experimental API, hardcoding it
)

// CompressBound returns the worst case size needed for a destination buffer,
// which can be used to preallocate a destination buffer or select a previously
// allocated buffer from a pool.
// See zstd.h to mirror implementation of ZSTD_COMPRESSBOUND
func CompressBound(srcSize int) int {
	lowLimit := 128 << 10 // 128 kB
	var margin int
	if srcSize < lowLimit {
		margin = (lowLimit - srcSize) >> 11
	}
	return srcSize + (srcSize >> 8) + margin
}

// cCompressBound is a cgo call to check the go implementation above against the c code.
func cCompressBound(srcSize int) int {
	__cgoprof_end_1 := prof.BeginCall("ad1223112c", "ZSTD_compressBound")

	// decompressSizeHint tries to give a hint on how much of the output buffer size we should have
	// based on zstd frame descriptors. To prevent DOS from maliciously-created payloads, limit the size
	__cgoprof_ret_2 := C.ZSTD_compressBound(C.size_t(srcSize))
	__cgoprof_end_1()
	return int(__cgoprof_ret_2)
}

func decompressSizeHint(src []byte) int {
	// 1 MB or 50x input size
	upperBound := 50 * len(src)
	if upperBound < decompressSizeBufferLimit {
		upperBound = decompressSizeBufferLimit
	}

	hint := upperBound
	if len(src) >= zstdFrameHeaderSizeMin {
		prof.PointerCheck("47a5b5ad9e", 50)
		__cgoprof_end_3 := prof.BeginCall("47a5b5ad9e", "ZSTD_getFrameContentSize")

		// On error, just use upperBound
		__cgoprof_ret_4 := C.ZSTD_getFrameContentSize(unsafe.Pointer(&src[0]), C.size_t(len(src)))
		__cgoprof_end_3()
		hint = int(__cgoprof_ret_4)
		if hint < 0 {
			hint = upperBound
		}
		if hint == 0 { // When compressing the empty slice, we need an output of at least 1 to pass down to the C lib
			hint = 1
		}
	}

	// Take the minimum of both
	if hint > upperBound {
		return upperBound
	}
	return hint
}

// Compress src into dst.  If you have a buffer to use, you can pass it to
// prevent allocation.  If it is too small, or if nil is passed, a new buffer
// will be allocated and returned.
func Compress(dst, src []byte) ([]byte, error) {
	return CompressLevel(dst, src, DefaultCompression)
}

// CompressLevel is the same as Compress but you can pass a compression level
func CompressLevel(dst, src []byte, level int) ([]byte, error) {
	bound := CompressBound(len(src))
	if cap(dst) >= bound {
		dst = dst[0:bound] // Reuse dst buffer
	} else {
		dst = make([]byte, bound)
	}

	// We need unsafe.Pointer(&src[0]) in the Cgo call to avoid "Go pointer to Go pointer" panics.
	// This means we need to special case empty input. See:
	// https://github.com/golang/go/issues/14210#issuecomment-346402945
	var cWritten C.size_t
	if len(src) == 0 {
		prof.PointerCheck("780103341b", 50)
		__cgoprof_end_5 := prof.BeginCall("780103341b", "ZSTD_compress")
		__cgoprof_ret_6 := C.ZSTD_compress(
			unsafe.Pointer(&dst[0]),
			C.size_t(len(dst)),
			unsafe.Pointer(nil),
			C.size_t(0),
			C.int(level))
		__cgoprof_end_5()
		cWritten = __cgoprof_ret_6

	} else {
		prof.PointerCheck("3bfca73bca", 50)
		__cgoprof_end_7 := prof.BeginCall("3bfca73bca", "ZSTD_compress")
		__cgoprof_ret_8 := C.ZSTD_compress(
			unsafe.Pointer(&dst[0]),
			C.size_t(len(dst)),
			unsafe.Pointer(&src[0]),
			C.size_t(len(src)),
			C.int(level))
		__cgoprof_end_7()
		cWritten = __cgoprof_ret_8

	}

	written := int(cWritten)
	// Check if the return is an Error code
	if err := getError(written); err != nil {
		return nil, err
	}
	return dst[:written], nil
}

// Decompress src into dst.  If you have a buffer to use, you can pass it to
// prevent allocation.  If it is too small, or if nil is passed, a new buffer
// will be allocated and returned.
func Decompress(dst, src []byte) ([]byte, error) {
	if len(src) == 0 {
		return []byte{}, ErrEmptySlice
	}

	bound := decompressSizeHint(src)
	if cap(dst) >= bound {
		dst = dst[0:cap(dst)]
	} else {
		dst = make([]byte, bound)
	}

	written, err := DecompressInto(dst, src)
	if err == nil {
		return dst[:written], nil
	}
	if !IsDstSizeTooSmallError(err) {
		return nil, err
	}

	// We failed getting a dst buffer of correct size, use stream API
	r := NewReader(bytes.NewReader(src))
	defer r.Close()
	return ioutil.ReadAll(r)
}

// DecompressInto decompresses src into dst. Unlike Decompress, DecompressInto
// requires that dst be sufficiently large to hold the decompressed payload.
// DecompressInto may be used when the caller knows the size of the decompressed
// payload before attempting decompression.
//
// It returns the number of bytes copied and an error if any is encountered. If
// dst is too small, DecompressInto errors.
func DecompressInto(dst, src []byte) (int, error) {
	prof.PointerCheck("1e0d98d4c8", 50)
	__cgoprof_end_9 := prof.BeginCall("1e0d98d4c8", "ZSTD_decompress")
	__cgoprof_ret_10 := C.ZSTD_decompress(
		unsafe.Pointer(&dst[0]),
		C.size_t(len(dst)),
		unsafe.Pointer(&src[0]),
		C.size_t(len(src)))
	__cgoprof_end_9()
	written := int(__cgoprof_ret_10)
	return written, getError(written)
}
