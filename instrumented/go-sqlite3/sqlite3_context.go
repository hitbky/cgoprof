// Copyright (C) 2019 Yasuhiro Matsumoto <mattn.jp@gmail.com>.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file.

package sqlite3

/*

#ifndef USE_LIBSQLITE3
#include "sqlite3-binding.h"
#else
#include <sqlite3.h>
#endif
#include <stdlib.h>
// These wrappers are necessary because SQLITE_TRANSIENT
// is a pointer constant, and cgo doesn't translate them correctly.

static inline void my_result_text(sqlite3_context *ctx, char *p, int np) {
	sqlite3_result_text(ctx, p, np, SQLITE_TRANSIENT);
}

static inline void my_result_blob(sqlite3_context *ctx, void *p, int np) {
	sqlite3_result_blob(ctx, p, np, SQLITE_TRANSIENT);
}
*/
import "C"

import (
	prof "cgoprof/runtime_go/cgoprof"
	"math"
	"unsafe"
)

const i64 = unsafe.Sizeof(int(0)) > 4

// SQLiteContext behave sqlite3_context
type SQLiteContext C.sqlite3_context

// ResultBool sets the result of an SQL function.
func (c *SQLiteContext) ResultBool(b bool) {
	if b {
		c.ResultInt(1)
	} else {
		c.ResultInt(0)
	}
}

// ResultBlob sets the result of an SQL function.
// See: sqlite3_result_blob, http://sqlite.org/c3ref/result_blob.html
func (c *SQLiteContext) ResultBlob(b []byte) {
	if i64 && len(b) > math.MaxInt32 {
		__cgoprof_end_1 := prof.BeginCall("d5f9d85f43", "sqlite3_result_error_toobig")
		C.sqlite3_result_error_toobig((*C.sqlite3_context)(c))
		__cgoprof_end_1()
		return
	}
	var p *byte
	if len(b) > 0 {
		p = &b[0]
	}
	prof.PointerCheck("0d9c547925", 50)
	__cgoprof_end_2 := prof.BeginCall("0d9c547925", "my_result_blob")
	C.my_result_blob((*C.sqlite3_context)(c), unsafe.Pointer(p), C.int(len(b)))
	__cgoprof_end_2(

	// ResultDouble sets the result of an SQL function.
	// See: sqlite3_result_double, http://sqlite.org/c3ref/result_blob.html
	)
}

func (c *SQLiteContext) ResultDouble(d float64) {
	__cgoprof_end_3 := prof.BeginCall("6ab1832fa2", "sqlite3_result_double")
	C.sqlite3_result_double((*C.sqlite3_context)(c), C.double(d))
	__cgoprof_end_3(

	// ResultInt sets the result of an SQL function.
	// See: sqlite3_result_int, http://sqlite.org/c3ref/result_blob.html
	)
}

func (c *SQLiteContext) ResultInt(i int) {
	if i64 && (i > math.MaxInt32 || i < math.MinInt32) {
		__cgoprof_end_4 := prof.BeginCall("74fe8df8c0", "sqlite3_result_int64")
		C.sqlite3_result_int64((*C.sqlite3_context)(c), C.sqlite3_int64(i))
		__cgoprof_end_4()
	} else {
		__cgoprof_end_5 := prof.BeginCall("60bcb722e4", "sqlite3_result_int")
		C.sqlite3_result_int((*C.sqlite3_context)(c), C.int(i))
		__cgoprof_end_5(

		// ResultInt64 sets the result of an SQL function.
		// See: sqlite3_result_int64, http://sqlite.org/c3ref/result_blob.html
		)
	}
}

func (c *SQLiteContext) ResultInt64(i int64) {
	__cgoprof_end_6 := prof.BeginCall("88e205450f", "sqlite3_result_int64")
	C.sqlite3_result_int64((*C.sqlite3_context)(c), C.sqlite3_int64(i))
	__cgoprof_end_6(

	// ResultNull sets the result of an SQL function.
	// See: sqlite3_result_null, http://sqlite.org/c3ref/result_blob.html
	)
}

func (c *SQLiteContext) ResultNull() {
	__cgoprof_end_7 := prof.BeginCall("c894cbcae7", "sqlite3_result_null")
	C.sqlite3_result_null((*C.sqlite3_context)(c))
	__cgoprof_end_7(

	// ResultText sets the result of an SQL function.
	// See: sqlite3_result_text, http://sqlite.org/c3ref/result_blob.html
	)
}

func (c *SQLiteContext) ResultText(s string) {
	if i64 && len(s) > math.MaxInt32 {
		__cgoprof_end_8 := prof.BeginCall("f13db44862", "sqlite3_result_error_toobig")
		C.sqlite3_result_error_toobig((*C.sqlite3_context)(c))
		__cgoprof_end_8()
		return
	}
	if len(s) == 0 {
		prof.PointerCheck("40ec7b51eb", 50)
		__cgoprof_end_9 := prof.BeginCall("40ec7b51eb", "my_result_text")
		C.my_result_text((*C.sqlite3_context)(c), (*C.char)(unsafe.Pointer(&placeHolder[0])), 0)
		__cgoprof_end_9()
		return
	}
	__cgoprof_end_10 := prof.BeginCall("6fce142435", "my_result_text")
	C.my_result_text((*C.sqlite3_context)(c), (*C.char)(unsafe.Pointer(unsafe.StringData(s))), C.int(len(s)))
	__cgoprof_end_10(

	// ResultZeroblob sets the result of an SQL function.
	// See: sqlite3_result_zeroblob, http://sqlite.org/c3ref/result_blob.html
	)
}

func (c *SQLiteContext) ResultZeroblob(n int) {
	__cgoprof_end_11 := prof.BeginCall("abc722a30a", "sqlite3_result_zeroblob")
	C.sqlite3_result_zeroblob((*C.sqlite3_context)(c), C.int(n))
	__cgoprof_end_11()
}
