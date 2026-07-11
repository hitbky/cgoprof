// Copyright (C) 2019 Yasuhiro Matsumoto <mattn.jp@gmail.com>.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file.

package sqlite3

// You can't export a Go function to C and have definitions in the C
// preamble in the same file, so we have to have callbackTrampoline in
// its own file. Because we need a separate file anyway, the support
// code for SQLite custom functions is in here.

/*
#ifndef USE_LIBSQLITE3
#include "sqlite3-binding.h"
#else
#include <sqlite3.h>
#endif
#include <stdlib.h>

void _sqlite3_result_text(sqlite3_context* ctx, const char* s, int n);
void _sqlite3_result_blob(sqlite3_context* ctx, const void* b, int l);
*/
import "C"

import (
	prof "cgoprof/runtime_go/cgoprof"
	"errors"
	"fmt"
	"math"
	"reflect"
	"sync"
	"unsafe"

	"time"
)

//export callbackTrampoline
func callbackTrampoline(ctx *C.sqlite3_context, argc int, argv **C.sqlite3_value) {
	__cgoprof_callbackStart_1 := time.Now()
	defer func() {
		prof.Callback("2082443546", "C", time.Since(__cgoprof_callbackStart_1))
	}()
	args := (*[(math.MaxInt32 - 1) / unsafe.Sizeof((*C.sqlite3_value)(nil))]*C.sqlite3_value)(unsafe.Pointer(argv))[:argc:argc]
	fi := lookupHandle(C.sqlite3_user_data(ctx)).(*functionInfo)
	fi.Call(ctx, args)
}

//export stepTrampoline
func stepTrampoline(ctx *C.sqlite3_context, argc C.int, argv **C.sqlite3_value) {
	__cgoprof_callbackStart_2 := time.Now()
	defer func() {
		prof.Callback("e3d1ce6d74", "C", time.Since(__cgoprof_callbackStart_2))
	}()
	args := (*[(math.MaxInt32 - 1) / unsafe.Sizeof((*C.sqlite3_value)(nil))]*C.sqlite3_value)(unsafe.Pointer(argv))[:int(argc):int(argc)]
	ai := lookupHandle(C.sqlite3_user_data(ctx)).(*aggInfo)
	ai.Step(ctx, args)
}

//export doneTrampoline
func doneTrampoline(ctx *C.sqlite3_context) {
	__cgoprof_callbackStart_3 := time.Now()
	defer func() {
		prof.Callback("1cdcdf66d4",

			"C", time.Since(__cgoprof_callbackStart_3))
	}()
	ai := lookupHandle(C.sqlite3_user_data(ctx)).(*aggInfo)
	ai.Done(ctx)
}

//export compareTrampoline
func compareTrampoline(handlePtr unsafe.Pointer, la C.int, a *C.char, lb C.int, b *C.char) C.int {
	__cgoprof_callbackStart_4 := time.Now()
	defer func() {
		prof.Callback("08f4e93f18", "C", time.Since(__cgoprof_callbackStart_4))
	}()
	cmp := lookupHandle(handlePtr).(func(string, string) int)
	prof.Conversion("935274f538", "C.GoStringN", int(la))
	__cgoprof_end_5 := prof.BeginCall(

		"935274f538", "GoStringN")
	__cgoprof_ret_6 := C.GoStringN(a, la)
	__cgoprof_end_5()
	prof.Conversion("e1872f8f7c", "C.GoStringN", int(lb))
	__cgoprof_end_7 := prof.BeginCall("e1872f8f7c", "GoStringN")
	__cgoprof_ret_8 := C.GoStringN(b, lb)
	__cgoprof_end_7()
	return C.int(cmp(__cgoprof_ret_6, __cgoprof_ret_8))
}

//export commitHookTrampoline
func commitHookTrampoline(handle unsafe.Pointer) int {
	__cgoprof_callbackStart_9 := time.Now()
	defer func() {
		prof.Callback("7cfdd22231",

			"C", time.Since(__cgoprof_callbackStart_9))
	}()
	callback := lookupHandle(handle).(func() int)
	return callback()
}

//export rollbackHookTrampoline
func rollbackHookTrampoline(handle unsafe.Pointer) {
	__cgoprof_callbackStart_10 := time.Now()
	defer func() {
		prof.Callback(

			"b82c21cb35", "C", time.Since(__cgoprof_callbackStart_10))
	}()
	callback := lookupHandle(handle).(func())
	callback()
}

//export updateHookTrampoline
func updateHookTrampoline(handle unsafe.Pointer, op int, db *C.char, table *C.char, rowid int64) {
	__cgoprof_callbackStart_11 := time.Now()
	defer func() {
		prof.Callback("2764a5bf24", "C", time.Since(__cgoprof_callbackStart_11))
	}()
	callback := lookupHandle(handle).(func(int, string, string, int64))
	prof.Conversion("4e5af08929", "C.GoString", int(0))
	__cgoprof_end_12 :=

		prof.BeginCall("4e5af08929", "GoString")
	__cgoprof_ret_13 := C.GoString(db)
	__cgoprof_end_12()
	prof.Conversion("ed1801fef5", "C.GoString", int(0))
	__cgoprof_end_14 := prof.BeginCall("ed1801fef5", "GoString")
	__cgoprof_ret_15 := C.GoString(table)
	__cgoprof_end_14()
	callback(op, __cgoprof_ret_13, __cgoprof_ret_15, rowid)
}

//export authorizerTrampoline
func authorizerTrampoline(handle unsafe.Pointer, op int, arg1 *C.char, arg2 *C.char, arg3 *C.char) int {
	__cgoprof_callbackStart_16 := time.Now()
	defer func() {
		prof.Callback("99825855c7", "C", time.Since(__cgoprof_callbackStart_16))
	}()
	callback := lookupHandle(handle).(func(int, string, string, string) int)
	prof.Conversion("a255d68df5", "C.GoString", int(0))
	__cgoprof_end_17 := prof.BeginCall(

		"a255d68df5", "GoString")
	__cgoprof_ret_18 := C.GoString(arg1)
	__cgoprof_end_17()
	prof.Conversion("1b32fe3ef7", "C.GoString", int(0))
	__cgoprof_end_19 := prof.BeginCall("1b32fe3ef7", "GoString")
	__cgoprof_ret_20 := C.GoString(arg2)
	__cgoprof_end_19()
	prof.Conversion("bcf85c7418", "C.GoString", int(0))
	__cgoprof_end_21 := prof.BeginCall("bcf85c7418", "GoString")
	__cgoprof_ret_22 := C.GoString(arg3)
	__cgoprof_end_21()
	return callback(op, __cgoprof_ret_18, __cgoprof_ret_20, __cgoprof_ret_22)
}

//export preUpdateHookTrampoline
func preUpdateHookTrampoline(handle unsafe.Pointer, dbHandle uintptr, op int, db *C.char, table *C.char, oldrowid int64, newrowid int64) {
	__cgoprof_callbackStart_23 := time.Now()
	defer func() {
		prof.Callback("b83c79e79f", "C", time.Since(__cgoprof_callbackStart_23))
	}()
	hval := lookupHandleVal(handle)
	data := SQLitePreUpdateData{
		Conn:         hval.db,
		Op:           op,
		DatabaseName: C.GoString(db),
		TableName:    C.GoString(table),
		OldRowID:     oldrowid,
		NewRowID:     newrowid,
	}
	callback := hval.val.(func(SQLitePreUpdateData))
	callback(data)
}

// Use handles to avoid passing Go pointers to C.
type handleVal struct {
	db  *SQLiteConn
	val any
}

var handleLock sync.Mutex
var handleVals = make(map[unsafe.Pointer]handleVal)

func newHandle(db *SQLiteConn, v any) unsafe.Pointer {
	handleLock.Lock()
	defer handleLock.Unlock()
	val := handleVal{db: db, val: v}
	prof.Memory("65c8963533", "malloc", int(C.size_t(1)))
	__cgoprof_end_24 := prof.BeginCall("65c8963533", "malloc")
	__cgoprof_ret_25 := C.malloc(C.size_t(1))
	__cgoprof_end_24()
	var p unsafe.Pointer = __cgoprof_ret_25
	if p == nil {
		panic("can't allocate 'cgo-pointer hack index pointer': ptr == nil")
	}
	handleVals[p] = val
	return p
}

func lookupHandleVal(handle unsafe.Pointer) handleVal {
	handleLock.Lock()
	defer handleLock.Unlock()
	return handleVals[handle]
}

func lookupHandle(handle unsafe.Pointer) any {
	return lookupHandleVal(handle).val
}

func deleteHandles(db *SQLiteConn) {
	handleLock.Lock()
	defer handleLock.Unlock()
	for handle, val := range handleVals {
		if val.db == db {
			delete(handleVals, handle)
			prof.Memory("4824e3bc4e", "free",

				// This is only here so that tests can refer to it.
				0)
			__cgoprof_end_26 := prof.BeginCall("4824e3bc4e", "free")
			C.free(handle)
			__cgoprof_end_26()
		}
	}
}

type callbackArgRaw C.sqlite3_value

type callbackArgConverter func(*C.sqlite3_value) (reflect.Value, error)

type callbackArgCast struct {
	f   callbackArgConverter
	typ reflect.Type
}

func (c callbackArgCast) Run(v *C.sqlite3_value) (reflect.Value, error) {
	val, err := c.f(v)
	if err != nil {
		return reflect.Value{}, err
	}
	if !val.Type().ConvertibleTo(c.typ) {
		return reflect.Value{}, fmt.Errorf("cannot convert %s to %s", val.Type(), c.typ)
	}
	return val.Convert(c.typ), nil
}

func callbackArgInt64(v *C.sqlite3_value) (reflect.Value, error) {
	__cgoprof_end_27 := prof.BeginCall("62e18549c8", "sqlite3_value_type")
	__cgoprof_ret_28 := C.sqlite3_value_type(v)
	__cgoprof_end_27()
	if __cgoprof_ret_28 != C.SQLITE_INTEGER {
		return reflect.Value{}, fmt.Errorf("argument must be an INTEGER")
	}
	__cgoprof_end_29 := prof.BeginCall("41bdbe068a", "sqlite3_value_int64")
	__cgoprof_ret_30 := C.sqlite3_value_int64(v)
	__cgoprof_end_29()
	return reflect.ValueOf(int64(__cgoprof_ret_30)), nil
}

func callbackArgBool(v *C.sqlite3_value) (reflect.Value, error) {
	__cgoprof_end_31 := prof.BeginCall("ea70ccbada", "sqlite3_value_type")
	__cgoprof_ret_32 := C.sqlite3_value_type(v)
	__cgoprof_end_31()
	if __cgoprof_ret_32 != C.SQLITE_INTEGER {
		return reflect.Value{}, fmt.Errorf("argument must be an INTEGER")
	}
	__cgoprof_end_33 := prof.BeginCall("d6d583069c", "sqlite3_value_int64")
	__cgoprof_ret_34 := C.sqlite3_value_int64(v)
	__cgoprof_end_33()
	i := int64(__cgoprof_ret_34)
	val := false
	if i != 0 {
		val = true
	}
	return reflect.ValueOf(val), nil
}

func callbackArgFloat64(v *C.sqlite3_value) (reflect.Value, error) {
	__cgoprof_end_35 := prof.BeginCall("cd7674b5b3", "sqlite3_value_type")
	__cgoprof_ret_36 := C.sqlite3_value_type(v)
	__cgoprof_end_35()
	if __cgoprof_ret_36 != C.SQLITE_FLOAT {
		return reflect.Value{}, fmt.Errorf("argument must be a FLOAT")
	}
	__cgoprof_end_37 := prof.BeginCall("f425b3a59c", "sqlite3_value_double")
	__cgoprof_ret_38 := C.sqlite3_value_double(v)
	__cgoprof_end_37()
	return reflect.ValueOf(float64(__cgoprof_ret_38)), nil
}

func callbackArgBytes(v *C.sqlite3_value) (reflect.Value, error) {
	__cgoprof_end_51 := prof.BeginCall("f98b9a9344", "sqlite3_value_type")
	__cgoprof_ret_52 := C.sqlite3_value_type(v)
	__cgoprof_end_51()
	switch __cgoprof_ret_52 {
	case C.SQLITE_BLOB:
		__cgoprof_end_39 := prof.BeginCall("a485ba87d7", "sqlite3_value_bytes")
		__cgoprof_ret_40 := C.sqlite3_value_bytes(v)
		__cgoprof_end_39()
		l := __cgoprof_ret_40
		__cgoprof_end_41 := prof.BeginCall("77ee12781a", "sqlite3_value_blob")
		__cgoprof_ret_42 := C.sqlite3_value_blob(v)
		__cgoprof_end_41()
		p := __cgoprof_ret_42
		prof.Conversion("a0cbe7ba7f", "C.GoBytes", int(l))
		__cgoprof_end_43 := prof.BeginCall("a0cbe7ba7f", "GoBytes")
		__cgoprof_ret_44 := C.GoBytes(p, l)
		__cgoprof_end_43()
		return reflect.ValueOf(__cgoprof_ret_44), nil
	case C.SQLITE_TEXT:
		__cgoprof_end_45 := prof.BeginCall("6d7b40b812", "sqlite3_value_bytes")
		__cgoprof_ret_46 := C.sqlite3_value_bytes(v)
		__cgoprof_end_45()
		l := __cgoprof_ret_46
		__cgoprof_end_47 := prof.BeginCall("c58d18e9e6", "sqlite3_value_text")
		__cgoprof_ret_48 := C.sqlite3_value_text(v)
		__cgoprof_end_47()
		c := unsafe.Pointer(__cgoprof_ret_48)
		prof.Conversion("27136d59d4", "C.GoBytes", int(l))
		__cgoprof_end_49 := prof.BeginCall("27136d59d4", "GoBytes")
		__cgoprof_ret_50 := C.GoBytes(c, l)
		__cgoprof_end_49()
		return reflect.ValueOf(__cgoprof_ret_50), nil
	default:
		return reflect.Value{}, fmt.Errorf("argument must be BLOB or TEXT")
	}
}

func callbackArgString(v *C.sqlite3_value) (reflect.Value, error) {
	__cgoprof_end_65 := prof.BeginCall("7d0bd66430", "sqlite3_value_type")
	__cgoprof_ret_66 := C.sqlite3_value_type(v)
	__cgoprof_end_65()
	switch __cgoprof_ret_66 {
	case C.SQLITE_BLOB:
		__cgoprof_end_53 := prof.BeginCall("a65de43531", "sqlite3_value_blob")
		__cgoprof_ret_54 := C.sqlite3_value_blob(v)
		__cgoprof_end_53()
		p := (*C.char)(__cgoprof_ret_54)
		__cgoprof_end_55 := prof.BeginCall("5460b93cde", "sqlite3_value_bytes")
		__cgoprof_ret_56 := C.sqlite3_value_bytes(v)
		__cgoprof_end_55()
		l := __cgoprof_ret_56
		prof.Conversion("bd3a0cf817", "C.GoStringN", int(l))
		__cgoprof_end_57 := prof.BeginCall("bd3a0cf817", "GoStringN")
		__cgoprof_ret_58 := C.GoStringN(p, l)
		__cgoprof_end_57()
		return reflect.ValueOf(__cgoprof_ret_58), nil
	case C.SQLITE_TEXT:
		__cgoprof_end_59 := prof.BeginCall("4a978d4d9d", "sqlite3_value_text")
		__cgoprof_ret_60 := C.sqlite3_value_text(v)
		__cgoprof_end_59()
		c := (*C.char)(unsafe.Pointer(__cgoprof_ret_60))
		__cgoprof_end_61 := prof.BeginCall("70ab3600f0", "sqlite3_value_bytes")
		__cgoprof_ret_62 := C.sqlite3_value_bytes(v)
		__cgoprof_end_61()
		l := __cgoprof_ret_62
		prof.Conversion("c2e7362245", "C.GoStringN", int(l))
		__cgoprof_end_63 := prof.BeginCall("c2e7362245", "GoStringN")
		__cgoprof_ret_64 := C.GoStringN(c, l)
		__cgoprof_end_63()
		return reflect.ValueOf(__cgoprof_ret_64), nil
	default:
		return reflect.Value{}, fmt.Errorf("argument must be BLOB or TEXT")
	}
}

func callbackArgGeneric(v *C.sqlite3_value) (reflect.Value, error) {
	__cgoprof_end_67 := prof.BeginCall("08d4a0ce94", "sqlite3_value_type")
	__cgoprof_ret_68 := C.sqlite3_value_type(v)
	__cgoprof_end_67()
	switch __cgoprof_ret_68 {
	case C.SQLITE_INTEGER:
		return callbackArgInt64(v)
	case C.SQLITE_FLOAT:
		return callbackArgFloat64(v)
	case C.SQLITE_TEXT:
		return callbackArgString(v)
	case C.SQLITE_BLOB:
		return callbackArgBytes(v)
	case C.SQLITE_NULL:
		// Interpret NULL as a nil byte slice.
		var ret []byte
		return reflect.ValueOf(ret), nil
	default:
		panic("unreachable")
	}
}

func callbackArg(typ reflect.Type) (callbackArgConverter, error) {
	switch typ.Kind() {
	case reflect.Interface:
		if typ.NumMethod() != 0 {
			return nil, errors.New("the only supported interface type is any")
		}
		return callbackArgGeneric, nil
	case reflect.Slice:
		if typ.Elem().Kind() != reflect.Uint8 {
			return nil, errors.New("the only supported slice type is []byte")
		}
		return callbackArgBytes, nil
	case reflect.String:
		return callbackArgString, nil
	case reflect.Bool:
		return callbackArgBool, nil
	case reflect.Int64:
		return callbackArgInt64, nil
	case reflect.Int8, reflect.Int16, reflect.Int32, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Int, reflect.Uint:
		c := callbackArgCast{callbackArgInt64, typ}
		return c.Run, nil
	case reflect.Float64:
		return callbackArgFloat64, nil
	case reflect.Float32:
		c := callbackArgCast{callbackArgFloat64, typ}
		return c.Run, nil
	default:
		return nil, fmt.Errorf("don't know how to convert to %s", typ)
	}
}

func callbackConvertArgs(argv []*C.sqlite3_value, converters []callbackArgConverter, variadic callbackArgConverter) ([]reflect.Value, error) {
	var args []reflect.Value

	if len(argv) < len(converters) {
		return nil, fmt.Errorf("function requires at least %d arguments", len(converters))
	}

	for i, arg := range argv[:len(converters)] {
		v, err := converters[i](arg)
		if err != nil {
			return nil, err
		}
		args = append(args, v)
	}

	if variadic != nil {
		for _, arg := range argv[len(converters):] {
			v, err := variadic(arg)
			if err != nil {
				return nil, err
			}
			args = append(args, v)
		}
	}
	return args, nil
}

type callbackRetConverter func(*C.sqlite3_context, reflect.Value) error

func callbackRetInteger(ctx *C.sqlite3_context, v reflect.Value) error {
	switch v.Type().Kind() {
	case reflect.Int64:
	case reflect.Int8, reflect.Int16, reflect.Int32, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Int, reflect.Uint:
		v = v.Convert(reflect.TypeOf(int64(0)))
	case reflect.Bool:
		b := v.Interface().(bool)
		if b {
			v = reflect.ValueOf(int64(1))
		} else {
			v = reflect.ValueOf(int64(0))
		}
	default:
		return fmt.Errorf("cannot convert %s to INTEGER", v.Type())
	}
	__cgoprof_end_69 := prof.BeginCall("c278bb6cfb", "sqlite3_result_int64")

	C.sqlite3_result_int64(ctx, C.sqlite3_int64(v.Interface().(int64)))
	__cgoprof_end_69()
	return nil
}

func callbackRetFloat(ctx *C.sqlite3_context, v reflect.Value) error {
	switch v.Type().Kind() {
	case reflect.Float64:
	case reflect.Float32:
		v = v.Convert(reflect.TypeOf(float64(0)))
	default:
		return fmt.Errorf("cannot convert %s to FLOAT", v.Type())
	}
	__cgoprof_end_70 := prof.BeginCall("e2ad664641", "sqlite3_result_double")

	C.sqlite3_result_double(ctx, C.double(v.Interface().(float64)))
	__cgoprof_end_70()
	return nil
}

func callbackRetBlob(ctx *C.sqlite3_context, v reflect.Value) error {
	if v.Type().Kind() != reflect.Slice || v.Type().Elem().Kind() != reflect.Uint8 {
		return fmt.Errorf("cannot convert %s to BLOB", v.Type())
	}
	i := v.Interface()
	if i == nil || len(i.([]byte)) == 0 {
		__cgoprof_end_71 := prof.BeginCall("e188b967c7", "sqlite3_result_null")
		C.sqlite3_result_null(ctx)
		__cgoprof_end_71()
	} else {
		bs := i.([]byte)
		if i64 && len(bs) > math.MaxInt32 {
			__cgoprof_end_72 := prof.BeginCall("ceb7f9d6d4", "sqlite3_result_error_toobig")
			C.sqlite3_result_error_toobig(ctx)
			__cgoprof_end_72()
			return nil
		}
		prof.PointerCheck("fe2bb4b8bb", 50)
		__cgoprof_end_73 := prof.BeginCall("fe2bb4b8bb", "_sqlite3_result_blob")
		C._sqlite3_result_blob(ctx, unsafe.Pointer(&bs[0]), C.int(len(bs)))
		__cgoprof_end_73()
	}
	return nil
}

func callbackRetText(ctx *C.sqlite3_context, v reflect.Value) error {
	if v.Type().Kind() != reflect.String {
		return fmt.Errorf("cannot convert %s to TEXT", v.Type())
	}
	s := v.Interface().(string)
	if i64 && len(s) > math.MaxInt32 {
		__cgoprof_end_74 := prof.BeginCall("ecc6f6d481", "sqlite3_result_error_toobig")
		C.sqlite3_result_error_toobig(ctx)
		__cgoprof_end_74()
		return nil
	}
	prof.Conversion("d753b7d599", "C.CString", int(len(s)+1))
	prof.Memory("d753b7d599", "malloc", int(len(s)+1))
	__cgoprof_end_75 := prof.BeginCall("d753b7d599", "CString")
	__cgoprof_ret_76 := C.CString(s)
	__cgoprof_end_75()
	cstr := __cgoprof_ret_76
	__cgoprof_end_77 := prof.BeginCall("02d89bc315", "_sqlite3_result_text")
	C._sqlite3_result_text(ctx, cstr, C.int(len(s)))
	__cgoprof_end_77()
	return nil
}

func callbackRetNil(ctx *C.sqlite3_context, v reflect.Value) error {
	return nil
}

func callbackRetGeneric(ctx *C.sqlite3_context, v reflect.Value) error {
	if v.IsNil() {
		__cgoprof_end_78 := prof.BeginCall("5ec1e2df7a", "sqlite3_result_null")
		C.sqlite3_result_null(ctx)
		__cgoprof_end_78()
		return nil
	}

	cb, err := callbackRet(v.Elem().Type())
	if err != nil {
		return err
	}

	return cb(ctx, v.Elem())
}

func callbackRet(typ reflect.Type) (callbackRetConverter, error) {
	switch typ.Kind() {
	case reflect.Interface:
		errorInterface := reflect.TypeOf((*error)(nil)).Elem()
		if typ.Implements(errorInterface) {
			return callbackRetNil, nil
		}

		if typ.NumMethod() == 0 {
			return callbackRetGeneric, nil
		}

		fallthrough
	case reflect.Slice:
		if typ.Elem().Kind() != reflect.Uint8 {
			return nil, errors.New("the only supported slice type is []byte")
		}
		return callbackRetBlob, nil
	case reflect.String:
		return callbackRetText, nil
	case reflect.Bool, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Int, reflect.Uint:
		return callbackRetInteger, nil
	case reflect.Float32, reflect.Float64:
		return callbackRetFloat, nil
	default:
		return nil, fmt.Errorf("don't know how to convert to %s", typ)
	}
}

func callbackError(ctx *C.sqlite3_context, err error) {
	prof.Conversion("be993e1b67", "C.CString", int(len(err.Error())+1))
	prof.Memory("be993e1b67", "malloc", int(len(err.Error())+1))
	__cgoprof_end_79 := prof.BeginCall("be993e1b67", "CString")
	__cgoprof_ret_80 := C.CString(err.Error())
	__cgoprof_end_79()
	cstr := __cgoprof_ret_80
	defer func() {
		prof.Memory("be993e1b67", "free", 0)
		__cgoprof_end_81 := prof.BeginCall("cb4e16b4cd", "free")
		C.free(unsafe.Pointer(cstr))
		__cgoprof_end_81()
	}()
	__cgoprof_end_82 := prof.BeginCall(

		// Test support code. Tests are not allowed to import "C", so we can't
		// declare any functions that use C.sqlite3_value.
		"0e2237157f", "sqlite3_result_error")
	C.sqlite3_result_error(ctx, cstr, C.int(-1))
	__cgoprof_end_82()
}

func callbackSyntheticForTests(v reflect.Value, err error) callbackArgConverter {
	return func(*C.sqlite3_value) (reflect.Value, error) {
		return v, err
	}
}
