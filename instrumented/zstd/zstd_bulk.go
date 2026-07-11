package zstd

/*
#include "zstd.h"
*/
import "C"
import (
	prof "cgoprof/runtime_go/cgoprof"
	"errors"
	"runtime"
	"unsafe"
)

var (
	// ErrEmptyDictionary is returned when the given dictionary is empty
	ErrEmptyDictionary = errors.New("Dictionary is empty")
	// ErrBadDictionary is returned when cannot load the given dictionary
	ErrBadDictionary = errors.New("Cannot load dictionary")
)

// BulkProcessor implements Bulk processing dictionary API.
// When compressing multiple messages or blocks using the same dictionary,
// it's recommended to digest the dictionary only once, since it's a costly operation.
// NewBulkProcessor() will create a state from digesting a dictionary.
// The resulting state can be used for future compression/decompression operations with very limited startup cost.
// BulkProcessor can be created once and shared by multiple threads concurrently, since its usage is read-only.
// The state will be freed when gc cleans up BulkProcessor.
type BulkProcessor struct {
	cDict *C.struct_ZSTD_CDict_s
	dDict *C.struct_ZSTD_DDict_s
}

// NewBulkProcessor creates a new BulkProcessor with a pre-trained dictionary and compression level
func NewBulkProcessor(dictionary []byte, compressionLevel int) (*BulkProcessor, error) {
	if len(dictionary) < 1 {
		return nil, ErrEmptyDictionary
	}

	p := &BulkProcessor{}
	runtime.SetFinalizer(p, finalizeBulkProcessor)
	prof.PointerCheck("32ae2df2c7", 50)
	__cgoprof_end_1 := prof.BeginCall("32ae2df2c7", "ZSTD_createCDict")
	__cgoprof_ret_2 := C.ZSTD_createCDict(
		unsafe.Pointer(&dictionary[0]),
		C.size_t(len(dictionary)),
		C.int(compressionLevel),
	)
	__cgoprof_end_1()
	p.cDict = __cgoprof_ret_2

	if p.cDict == nil {
		return nil, ErrBadDictionary
	}
	prof.PointerCheck("50ad5844c4", 50)
	__cgoprof_end_3 := prof.BeginCall("50ad5844c4", "ZSTD_createDDict")
	__cgoprof_ret_4 := C.ZSTD_createDDict(
		unsafe.Pointer(&dictionary[0]),
		C.size_t(len(dictionary)),
	)
	__cgoprof_end_3()
	p.dDict = __cgoprof_ret_4

	if p.dDict == nil {
		return nil, ErrBadDictionary
	}

	return p, nil
}

// Compress compresses `src` into `dst` with the dictionary given when creating the BulkProcessor.
// If you have a buffer to use, you can pass it to prevent allocation.
// If it is too small, or if nil is passed, a new buffer will be allocated and returned.
func (p *BulkProcessor) Compress(dst, src []byte) ([]byte, error) {
	bound := CompressBound(len(src))
	if cap(dst) >= bound {
		dst = dst[0:bound]
	} else {
		dst = make([]byte, bound)
	}
	__cgoprof_end_5 := prof.BeginCall("7642f5965c",

		// We need unsafe.Pointer(&src[0]) in the Cgo call to avoid "Go pointer to Go pointer" panics.
		// This means we need to special case empty input. See:
		// https://github.com/golang/go/issues/14210#issuecomment-346402945
		"ZSTD_createCCtx")
	__cgoprof_ret_6 := C.ZSTD_createCCtx()
	__cgoprof_end_5()
	cctx := __cgoprof_ret_6

	var cWritten C.size_t
	if len(src) == 0 {
		prof.PointerCheck("da50a33d01", 50)
		__cgoprof_end_7 := prof.BeginCall("da50a33d01", "ZSTD_compress_usingCDict")
		__cgoprof_ret_8 := C.ZSTD_compress_usingCDict(
			cctx,
			unsafe.Pointer(&dst[0]),
			C.size_t(len(dst)),
			unsafe.Pointer(nil),
			C.size_t(len(src)),
			p.cDict,
		)
		__cgoprof_end_7()
		cWritten = __cgoprof_ret_8

	} else {
		prof.PointerCheck("b6f10c40af", 50)
		__cgoprof_end_9 := prof.BeginCall("b6f10c40af", "ZSTD_compress_usingCDict")
		__cgoprof_ret_10 := C.ZSTD_compress_usingCDict(
			cctx,
			unsafe.Pointer(&dst[0]),
			C.size_t(len(dst)),
			unsafe.Pointer(&src[0]),
			C.size_t(len(src)),
			p.cDict,
		)
		__cgoprof_end_9()
		cWritten = __cgoprof_ret_10

	}
	__cgoprof_end_11 := prof.BeginCall("aa3120c1d6", "ZSTD_freeCCtx")

	C.ZSTD_freeCCtx(cctx)
	__cgoprof_end_11()

	written := int(cWritten)
	if err := getError(written); err != nil {
		return nil, err
	}
	return dst[:written], nil
}

// Decompress decompresses `src` into `dst` with the dictionary given when creating the BulkProcessor.
// If you have a buffer to use, you can pass it to prevent allocation.
// If it is too small, or if nil is passed, a new buffer will be allocated and returned.
func (p *BulkProcessor) Decompress(dst, src []byte) ([]byte, error) {
	if len(src) == 0 {
		return nil, ErrEmptySlice
	}

	contentSize := decompressSizeHint(src)
	if cap(dst) >= contentSize {
		dst = dst[0:cap(dst)]
	} else {
		dst = make([]byte, contentSize)
	}

	if len(dst) == 0 {
		return dst, nil
	}
	__cgoprof_end_12 := prof.BeginCall("e7596ec17b", "ZSTD_createDCtx")
	__cgoprof_ret_13 := C.ZSTD_createDCtx()
	__cgoprof_end_12()
	dctx := __cgoprof_ret_13
	prof.PointerCheck("2f168891b7", 50)
	__cgoprof_end_14 := prof.BeginCall("2f168891b7", "ZSTD_decompress_usingDDict")
	__cgoprof_ret_15 := C.ZSTD_decompress_usingDDict(
		dctx,
		unsafe.Pointer(&dst[0]),
		C.size_t(len(dst)),
		unsafe.Pointer(&src[0]),
		C.size_t(len(src)),
		p.dDict,
	)
	__cgoprof_end_14()
	cWritten := __cgoprof_ret_15
	__cgoprof_end_16 := prof.BeginCall("b0dd6689ea", "ZSTD_freeDCtx")

	C.ZSTD_freeDCtx(dctx)
	__cgoprof_end_16()

	written := int(cWritten)
	if err := getError(written); err != nil {
		return nil, err
	}

	return dst[:written], nil
}

// finalizeBulkProcessor frees compression and decompression dictionaries from memory
func finalizeBulkProcessor(p *BulkProcessor) {
	if p.cDict != nil {
		__cgoprof_end_17 := prof.BeginCall("cf22c1d399", "ZSTD_freeCDict")
		C.ZSTD_freeCDict(p.cDict)
		__cgoprof_end_17()
	}
	if p.dDict != nil {
		__cgoprof_end_18 := prof.BeginCall("f6e5f2fa46", "ZSTD_freeDDict")
		C.ZSTD_freeDDict(p.dDict)
		__cgoprof_end_18()
	}
}
