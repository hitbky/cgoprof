package zstd

/*
#include "zstd.h"
*/
import "C"
import

// ErrorCode is an error returned by the zstd library.
prof "cgoprof/runtime_go/cgoprof"

type ErrorCode int

// Error returns the error string given by zstd
func (e ErrorCode) Error() string {
	__cgoprof_end_1 := prof.BeginCall("a1ff806bd6", "ZSTD_getErrorName")
	__cgoprof_ret_2 := C.ZSTD_getErrorName(C.size_t(e))
	__cgoprof_end_1()
	prof.Conversion("33032ebc66", "C.GoString", int(0))
	__cgoprof_end_3 := prof.BeginCall(

		// getError returns an error for the return code, or nil if it's not an error
		"33032ebc66", "GoString")
	__cgoprof_ret_4 := C.GoString(__cgoprof_ret_2)
	__cgoprof_end_3()
	return __cgoprof_ret_4
}

func cIsError(code int) bool {
	__cgoprof_end_5 := prof.BeginCall("4b06c53a01", "ZSTD_isError")
	__cgoprof_ret_6 := C.ZSTD_isError(C.size_t(code))
	__cgoprof_end_5()
	return int(__cgoprof_ret_6) != 0
}

func getError(code int) error {
	if code < 0 && cIsError(code) {
		return ErrorCode(code)
	}
	return nil
}

// IsDstSizeTooSmallError returns whether the error correspond to zstd standard sDstSizeTooSmall error
func IsDstSizeTooSmallError(e error) bool {
	if e != nil && e.Error() == "Destination buffer is too small" {
		return true
	}
	return false
}
