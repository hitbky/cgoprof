//go:build sqlite_column_metadata
// +build sqlite_column_metadata

package sqlite3

/*
#ifndef USE_LIBSQLITE3
#cgo CFLAGS: -DSQLITE_ENABLE_COLUMN_METADATA
#include "sqlite3-binding.h"
#else
#include <sqlite3.h>
#endif
*/
import "C"
import

// ColumnTableName returns the table that is the origin of a particular result
// column in a SELECT statement.
//
// See https://www.sqlite.org/c3ref/column_database_name.html
prof "cgoprof/runtime_go/cgoprof"

func (s *SQLiteStmt) ColumnTableName(n int) string {
	__cgoprof_end_1 := prof.BeginCall("7f2712453a", "sqlite3_column_table_name")
	__cgoprof_ret_2 := C.sqlite3_column_table_name(s.s, C.int(n))
	__cgoprof_end_1()
	prof.Conversion("be1ebd3f90", "C.GoString", int(0))
	__cgoprof_end_3 := prof.BeginCall("be1ebd3f90", "GoString")
	__cgoprof_ret_4 := C.GoString(__cgoprof_ret_2)
	__cgoprof_end_3()
	return __cgoprof_ret_4
}
