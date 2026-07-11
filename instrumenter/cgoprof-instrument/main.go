package main

import (
	"bytes"
	"crypto/sha1"
	"encoding/hex"
	"flag"
	"fmt"
	"go/ast"
	"go/format"
	"go/parser"
	"go/printer"
	"go/token"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

const runtimeImportPath = "cgoprof/runtime_go/cgoprof"

var cTypeNames = map[string]bool{
	"char": true, "double": true, "float": true, "int": true, "long": true,
	"longlong": true, "short": true, "uchar": true, "uint": true,
	"ulong": true, "ulonglong": true, "ushort": true, "sizeof": true,
	"size_t": true, "ssize_t": true, "uintptr_t": true,
	"sqlite3_int64": true, "sqlite3_uint64": true,
}

type instrumenter struct {
	fset          *token.FileSet
	file          *ast.File
	relPath       string
	summaries     map[string]funcSummary
	tempIndex     int
	changed       bool
	needProf      bool
	needTime      bool
	pointerVars   map[string]bool
	cAllocatedVar map[string]string
}

type pointerOrigin int

const (
	originUnknown pointerOrigin = iota
	originGo
	originC
)

type funcSummary struct {
	returnGoPointer bool
	returnCPointer  bool
	returnParam     int
}

func main() {
	in := flag.String("in", "", "input Go module or package directory")
	out := flag.String("out", "", "output directory for the instrumented copy")
	runtimePath := flag.String("runtime", "", "absolute or relative path to runtime_go/cgoprof")
	force := flag.Bool("force", false, "overwrite output directory if it already exists")
	flag.Parse()

	if *in == "" || *out == "" {
		fmt.Fprintln(os.Stderr, "usage: cgoprof-instrument -in <project> -out <instrumented-copy> [-runtime <path>] [-force]")
		os.Exit(2)
	}
	if err := run(*in, *out, *runtimePath, *force); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run(inRoot string, outRoot string, runtimePath string, force bool) error {
	inAbs, err := filepath.Abs(inRoot)
	if err != nil {
		return err
	}
	outAbs, err := filepath.Abs(outRoot)
	if err != nil {
		return err
	}
	if runtimePath != "" {
		runtimePath, err = filepath.Abs(runtimePath)
		if err != nil {
			return err
		}
	}
	if _, err := os.Stat(outAbs); err == nil {
		if !force {
			return fmt.Errorf("output directory already exists: %s", outAbs)
		}
		if err := os.RemoveAll(outAbs); err != nil {
			return err
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	if err := copyTree(inAbs, outAbs); err != nil {
		return err
	}
	summaries, err := collectFunctionSummaries(outAbs)
	if err != nil {
		return err
	}
	if err := filepath.WalkDir(outAbs, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			name := entry.Name()
			if name == ".git" || name == "vendor" {
				return filepath.SkipDir
			}
			return nil
		}
		if filepath.Ext(path) != ".go" {
			return nil
		}
		rel, err := filepath.Rel(outAbs, path)
		if err != nil {
			return err
		}
		return instrumentFile(path, filepath.ToSlash(rel), summaries)
	}); err != nil {
		return err
	}
	if runtimePath != "" {
		return updateGoMod(outAbs, runtimePath)
	}
	return nil
}

func copyTree(src string, dst string) error {
	return filepath.WalkDir(src, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		rel, err := filepath.Rel(src, path)
		if err != nil {
			return err
		}
		if rel == "." {
			return os.MkdirAll(dst, 0o755)
		}
		if entry.IsDir() {
			if entry.Name() == ".git" {
				return filepath.SkipDir
			}
			return os.MkdirAll(filepath.Join(dst, rel), 0o755)
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		return copyFile(path, filepath.Join(dst, rel), info.Mode())
	})
}

func copyFile(src string, dst string, mode fs.FileMode) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_EXCL|os.O_WRONLY, mode)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		_ = out.Close()
		return err
	}
	return out.Close()
}

func collectFunctionSummaries(root string) (map[string]funcSummary, error) {
	files := map[string]*ast.File{}
	if err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			name := entry.Name()
			if name == ".git" || name == "vendor" {
				return filepath.SkipDir
			}
			return nil
		}
		if filepath.Ext(path) != ".go" {
			return nil
		}
		text, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		fset := token.NewFileSet()
		file, err := parser.ParseFile(fset, path, text, parser.ParseComments)
		if err != nil {
			return err
		}
		pkgKey := filepath.ToSlash(filepath.Dir(path))
		files[pkgKey+"\x00"+file.Name.Name] = file
		return nil
	}); err != nil {
		return nil, err
	}
	summaries := map[string]funcSummary{}
	for i := 0; i < 4; i++ {
		changed := false
		for pkgKey, file := range files {
			for _, decl := range file.Decls {
				fn, ok := decl.(*ast.FuncDecl)
				if !ok || fn.Body == nil {
					continue
				}
				key := summaryKey(pkgKey, fn.Name.Name)
				next := summarizeFunction(fn, summaries, pkgKey)
				if next != summaries[key] {
					summaries[key] = next
					changed = true
				}
			}
		}
		if !changed {
			break
		}
	}
	return summaries, nil
}

func summaryKey(pkgKey string, name string) string {
	return pkgKey + "\x00" + name
}

func packageSummaries(all map[string]funcSummary, pkgKey string) map[string]funcSummary {
	prefix := pkgKey + "\x00"
	local := map[string]funcSummary{}
	for key, summary := range all {
		if strings.HasPrefix(key, prefix) {
			local[strings.TrimPrefix(key, prefix)] = summary
		}
	}
	return local
}

func summarizeFunction(fn *ast.FuncDecl, summaries map[string]funcSummary, pkgKey string) funcSummary {
	paramIndex := map[string]int{}
	idx := 0
	if fn.Type.Params != nil {
		for _, field := range fn.Type.Params.List {
			for _, name := range field.Names {
				paramIndex[name.Name] = idx
				idx++
			}
			if len(field.Names) == 0 {
				idx++
			}
		}
	}
	localGoPointers := map[string]bool{}
	localCPointers := map[string]string{}
	summary := funcSummary{returnParam: -1}
	var visitStmts func([]ast.Stmt)
	visitStmts = func(stmts []ast.Stmt) {
		for _, stmt := range stmts {
			switch s := stmt.(type) {
			case *ast.AssignStmt:
				captureSummaryAssignments(s.Lhs, s.Rhs, localGoPointers, localCPointers, summaries, pkgKey)
			case *ast.DeclStmt:
				if gen, ok := s.Decl.(*ast.GenDecl); ok {
					for _, spec := range gen.Specs {
						if valueSpec, ok := spec.(*ast.ValueSpec); ok {
							lhs := make([]ast.Expr, 0, len(valueSpec.Names))
							for _, name := range valueSpec.Names {
								lhs = append(lhs, name)
							}
							captureSummaryAssignments(lhs, valueSpec.Values, localGoPointers, localCPointers, summaries, pkgKey)
						}
					}
				}
			case *ast.ReturnStmt:
				for _, expr := range s.Results {
					switch pointerOriginOf(expr, localCPointers, localGoPointers, packageSummaries(summaries, pkgKey), pkgKey) {
					case originGo:
						summary.returnGoPointer = true
					case originC:
						summary.returnCPointer = true
					}
					if name, ok := expr.(*ast.Ident); ok {
						if param, ok := paramIndex[name.Name]; ok && summary.returnParam < 0 {
							summary.returnParam = param
						}
					}
				}
			case *ast.BlockStmt:
				visitStmts(s.List)
			case *ast.IfStmt:
				if s.Body != nil {
					visitStmts(s.Body.List)
				}
				if block, ok := s.Else.(*ast.BlockStmt); ok {
					visitStmts(block.List)
				}
			case *ast.ForStmt:
				if s.Body != nil {
					visitStmts(s.Body.List)
				}
			case *ast.RangeStmt:
				if s.Body != nil {
					visitStmts(s.Body.List)
				}
			case *ast.SwitchStmt:
				if s.Body != nil {
					for _, clause := range s.Body.List {
						if cc, ok := clause.(*ast.CaseClause); ok {
							visitStmts(cc.Body)
						}
					}
				}
			}
		}
	}
	visitStmts(fn.Body.List)
	return summary
}

func captureSummaryAssignments(lhs []ast.Expr, rhs []ast.Expr, localGoPointers map[string]bool, localCPointers map[string]string, summaries map[string]funcSummary, pkgKey string) {
	for idx, left := range lhs {
		name, ok := assignedIdentName(left)
		if !ok {
			continue
		}
		var right ast.Expr
		if len(rhs) == 1 {
			right = rhs[0]
		} else if idx < len(rhs) {
			right = rhs[idx]
		}
		if right == nil {
			continue
		}
		delete(localGoPointers, name)
		delete(localCPointers, name)
		switch pointerOriginOf(right, localCPointers, localGoPointers, packageSummaries(summaries, pkgKey), pkgKey) {
		case originGo:
			localGoPointers[name] = true
		case originC:
			localCPointers[name] = "__summary_cptr"
		}
	}
}

func instrumentFile(path string, relPath string, summaries map[string]funcSummary) error {
	text, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if !bytes.Contains(text, []byte(`import "C"`)) && !bytes.Contains(text, []byte(`"C"`)) {
		return nil
	}
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, path, text, parser.ParseComments)
	if err != nil {
		return err
	}
	if !importsC(file) {
		return nil
	}
	pkgKey := filepath.ToSlash(filepath.Dir(path)) + "\x00" + file.Name.Name
	inst := &instrumenter{fset: fset, file: file, relPath: relPath, summaries: packageSummaries(summaries, pkgKey)}
	exportedNames := map[string]bool{}
	for _, decl := range file.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || fn.Body == nil {
			continue
		}
		inst.pointerVars = map[string]bool{}
		inst.cAllocatedVar = map[string]string{}
		if exportedName, ok := exportDirectiveName(fn.Doc); ok {
			exportedNames[exportedName] = true
			inst.instrumentCallback(fn, exportedName)
		}
		fn.Body.List = inst.rewriteStmtList(fn.Body.List)
	}
	if !inst.changed {
		return nil
	}
	if inst.needProf {
		addNamedImport(file, "prof", runtimeImportPath)
	}
	if inst.needTime {
		addNamedImport(file, "", "time")
	}
	var buf bytes.Buffer
	if err := printer.Fprint(&buf, fset, file); err != nil {
		return err
	}
	formatted, err := format.Source(buf.Bytes())
	if err != nil {
		return err
	}
	if len(exportedNames) > 0 {
		formatted = []byte(repairExportDirectives(string(formatted), exportedNames))
	}
	return os.WriteFile(path, formatted, 0o644)
}

func importsC(file *ast.File) bool {
	for _, imp := range file.Imports {
		if strings.Trim(imp.Path.Value, `"`) == "C" {
			return true
		}
	}
	return false
}

func (i *instrumenter) instrumentCallback(fn *ast.FuncDecl, exportedName string) {
	siteID := i.siteID(fn.Pos(), "callback:"+exportedName)
	startName := i.nextTemp("callbackStart")
	start := assignStmt(startName, callExpr(sel(ident("time"), "Now")))
	deferStmt := &ast.DeferStmt{
		Call: callExpr(&ast.FuncLit{
			Type: &ast.FuncType{Params: &ast.FieldList{}},
			Body: &ast.BlockStmt{List: []ast.Stmt{
				exprStmt(callExpr(sel(ident("prof"), "Callback"),
					stringLit(siteID),
					stringLit("C"),
					callExpr(sel(ident("time"), "Since"), ident(startName)),
				)),
			}},
		}),
	}
	fn.Body.List = append([]ast.Stmt{start, deferStmt}, fn.Body.List...)
	i.changed = true
	i.needProf = true
	i.needTime = true
}

func (i *instrumenter) rewriteStmtList(stmts []ast.Stmt) []ast.Stmt {
	out := make([]ast.Stmt, 0, len(stmts))
	for _, stmt := range stmts {
		out = append(out, i.rewriteStmt(stmt)...)
	}
	return out
}

func (i *instrumenter) rewriteStmt(stmt ast.Stmt) []ast.Stmt {
	switch s := stmt.(type) {
	case *ast.BlockStmt:
		s.List = i.rewriteStmtList(s.List)
		return []ast.Stmt{s}
	case *ast.ForStmt:
		if s.Body != nil {
			s.Body.List = i.rewriteStmtList(s.Body.List)
		}
	case *ast.RangeStmt:
		if s.Body != nil {
			s.Body.List = i.rewriteStmtList(s.Body.List)
		}
	case *ast.IfStmt:
		if s.Body != nil {
			s.Body.List = i.rewriteStmtList(s.Body.List)
		}
		if s.Else != nil {
			s.Else = i.rewriteElse(s.Else)
		}
	case *ast.SwitchStmt:
		if s.Body != nil {
			for _, clause := range s.Body.List {
				if cc, ok := clause.(*ast.CaseClause); ok {
					cc.Body = i.rewriteStmtList(cc.Body)
				}
			}
		}
	case *ast.TypeSwitchStmt:
		if s.Body != nil {
			for _, clause := range s.Body.List {
				if cc, ok := clause.(*ast.CaseClause); ok {
					cc.Body = i.rewriteStmtList(cc.Body)
				}
			}
		}
	case *ast.SelectStmt:
		if s.Body != nil {
			for _, clause := range s.Body.List {
				if cc, ok := clause.(*ast.CommClause); ok {
					cc.Body = i.rewriteStmtList(cc.Body)
				}
			}
		}
	}
	pre := []ast.Stmt{}
	rw := &exprRewriter{inst: i, pre: &pre}
	rewritten := rw.rewriteStmtExprs(stmt)
	if len(pre) == 0 {
		return []ast.Stmt{rewritten}
	}
	i.changed = true
	return append(pre, rewritten)
}

func (i *instrumenter) rewriteElse(stmt ast.Stmt) ast.Stmt {
	if block, ok := stmt.(*ast.BlockStmt); ok {
		block.List = i.rewriteStmtList(block.List)
		return block
	}
	if nested, ok := stmt.(*ast.IfStmt); ok {
		res := i.rewriteStmt(nested)
		if len(res) == 1 {
			return res[0]
		}
		return &ast.BlockStmt{List: res}
	}
	return stmt
}

type exprRewriter struct {
	inst *instrumenter
	pre  *[]ast.Stmt
}

func (r *exprRewriter) rewriteStmtExprs(stmt ast.Stmt) ast.Stmt {
	switch s := stmt.(type) {
	case *ast.AssignStmt:
		r.captureAssignments(s.Lhs, s.Rhs)
		for idx, expr := range s.Rhs {
			s.Rhs[idx] = r.rewriteExpr(expr)
		}
	case *ast.ExprStmt:
		if call, ok := s.X.(*ast.CallExpr); ok {
			if sym, ok := cCallSymbol(call); ok && !cTypeNames[sym] {
				r.instrumentVoidCCall(call, sym)
				return &ast.EmptyStmt{}
			}
		}
		s.X = r.rewriteExpr(s.X)
	case *ast.ReturnStmt:
		for idx, expr := range s.Results {
			s.Results[idx] = r.rewriteExpr(expr)
		}
	case *ast.SendStmt:
		s.Value = r.rewriteExpr(s.Value)
	case *ast.DeferStmt:
		if call := s.Call; call != nil {
			if sym, ok := cCallSymbol(call); ok && !cTypeNames[sym] {
				return r.instrumentDeferredCCall(call, sym)
			}
			s.Call = r.rewriteExpr(call).(*ast.CallExpr)
		}
	case *ast.GoStmt:
		if call := s.Call; call != nil {
			s.Call = r.rewriteExpr(call).(*ast.CallExpr)
		}
	case *ast.DeclStmt:
		if gen, ok := s.Decl.(*ast.GenDecl); ok {
			for _, spec := range gen.Specs {
				valueSpec, ok := spec.(*ast.ValueSpec)
				if !ok {
					continue
				}
				r.captureValueSpec(valueSpec)
				for idx, expr := range valueSpec.Values {
					valueSpec.Values[idx] = r.rewriteExpr(expr)
				}
			}
		}
	case *ast.IfStmt:
		if s.Cond != nil {
			s.Cond = r.rewriteExpr(s.Cond)
		}
	case *ast.ForStmt:
		// Rewriting a cgo call in the loop condition by hoisting it before the
		// loop would change per-iteration evaluation semantics. Leave it for a
		// later control-flow preserving lowering pass.
	case *ast.RangeStmt:
		if s.X != nil {
			s.X = r.rewriteExpr(s.X)
		}
	case *ast.SwitchStmt:
		if s.Tag != nil {
			s.Tag = r.rewriteExpr(s.Tag)
		}
	case *ast.IncDecStmt:
		return stmt
	}
	return stmt
}

func (r *exprRewriter) captureAssignments(lhs []ast.Expr, rhs []ast.Expr) {
	for idx, left := range lhs {
		name, ok := assignedIdentName(left)
		if !ok {
			continue
		}
		var right ast.Expr
		if len(rhs) == 1 {
			right = rhs[0]
		} else if idx < len(rhs) {
			right = rhs[idx]
		}
		if right == nil {
			continue
		}
		delete(r.inst.pointerVars, name)
		delete(r.inst.cAllocatedVar, name)
		switch pointerOriginOf(right, r.inst.cAllocatedVar, r.inst.pointerVars, r.inst.summaries, "") {
		case originGo:
			r.inst.pointerVars[name] = true
		case originC:
			r.inst.cAllocatedVar[name] = "__summary_cptr"
		}
		if call, ok := right.(*ast.CallExpr); ok {
			if sym, ok := cCallSymbol(call); ok && isCAllocationSymbol(sym) {
				r.inst.cAllocatedVar[name] = r.inst.siteID(call.Pos(), sym)
			}
		}
	}
}

func (r *exprRewriter) captureValueSpec(spec *ast.ValueSpec) {
	lhs := make([]ast.Expr, 0, len(spec.Names))
	for _, name := range spec.Names {
		lhs = append(lhs, name)
	}
	r.captureAssignments(lhs, spec.Values)
}

func (r *exprRewriter) rewriteExpr(expr ast.Expr) ast.Expr {
	switch e := expr.(type) {
	case *ast.CallExpr:
		if sym, ok := cCallSymbol(e); ok && !cTypeNames[sym] {
			return r.instrumentValueCCall(e, sym)
		}
		e.Fun = r.rewriteExpr(e.Fun)
		for idx, arg := range e.Args {
			e.Args[idx] = r.rewriteExpr(arg)
		}
		return e
	case *ast.UnaryExpr:
		e.X = r.rewriteExpr(e.X)
	case *ast.BinaryExpr:
		e.X = r.rewriteExpr(e.X)
		e.Y = r.rewriteExpr(e.Y)
	case *ast.ParenExpr:
		e.X = r.rewriteExpr(e.X)
	case *ast.IndexExpr:
		e.X = r.rewriteExpr(e.X)
		e.Index = r.rewriteExpr(e.Index)
	case *ast.IndexListExpr:
		e.X = r.rewriteExpr(e.X)
		for idx, ind := range e.Indices {
			e.Indices[idx] = r.rewriteExpr(ind)
		}
	case *ast.SliceExpr:
		e.X = r.rewriteExpr(e.X)
		if e.Low != nil {
			e.Low = r.rewriteExpr(e.Low)
		}
		if e.High != nil {
			e.High = r.rewriteExpr(e.High)
		}
		if e.Max != nil {
			e.Max = r.rewriteExpr(e.Max)
		}
	case *ast.StarExpr:
		e.X = r.rewriteExpr(e.X)
	case *ast.SelectorExpr:
		e.X = r.rewriteExpr(e.X)
	}
	return expr
}

func (r *exprRewriter) instrumentValueCCall(call *ast.CallExpr, sym string) ast.Expr {
	siteID := r.inst.siteID(call.Pos(), sym)
	for idx, arg := range call.Args {
		call.Args[idx] = r.rewriteExpr(arg)
	}
	r.addConversionEvents(call, sym, siteID)
	r.addAllocationEvents(call, sym, siteID)
	r.addPointerCheck(call, siteID)
	endName := r.inst.nextTemp("end")
	tempName := r.inst.nextTemp("ret")
	*r.pre = append(*r.pre,
		assignStmt(endName, callExpr(sel(ident("prof"), "BeginCall"), stringLit(siteID), stringLit(sym))),
		assignStmt(tempName, call),
		exprStmt(callExpr(ident(endName))),
	)
	r.inst.needProf = true
	return ident(tempName)
}

func (r *exprRewriter) instrumentVoidCCall(call *ast.CallExpr, sym string) {
	siteID := r.inst.siteID(call.Pos(), sym)
	for idx, arg := range call.Args {
		call.Args[idx] = r.rewriteExpr(arg)
	}
	r.addConversionEvents(call, sym, siteID)
	r.addAllocationEvents(call, sym, siteID)
	if sym == "free" {
		freeSiteID := r.freeSiteID(call, siteID)
		*r.pre = append(*r.pre, exprStmt(callExpr(sel(ident("prof"), "Memory"), stringLit(freeSiteID), stringLit("free"), intLit(0))))
	}
	r.addPointerCheck(call, siteID)
	endName := r.inst.nextTemp("end")
	*r.pre = append(*r.pre,
		assignStmt(endName, callExpr(sel(ident("prof"), "BeginCall"), stringLit(siteID), stringLit(sym))),
		exprStmt(call),
		exprStmt(callExpr(ident(endName))),
	)
	r.inst.needProf = true
}

func (r *exprRewriter) instrumentDeferredCCall(call *ast.CallExpr, sym string) ast.Stmt {
	siteID := r.inst.siteID(call.Pos(), sym)
	body := []ast.Stmt{}
	if sym == "free" {
		body = append(body, exprStmt(callExpr(sel(ident("prof"), "Memory"), stringLit(r.freeSiteID(call, siteID)), stringLit("free"), intLit(0))))
	}
	r.addPointerCheckTo(&body, call, siteID)
	endName := r.inst.nextTemp("end")
	body = append(body,
		assignStmt(endName, callExpr(sel(ident("prof"), "BeginCall"), stringLit(siteID), stringLit(sym))),
		exprStmt(call),
		exprStmt(callExpr(ident(endName))),
	)
	r.inst.changed = true
	r.inst.needProf = true
	return &ast.DeferStmt{
		Call: callExpr(&ast.FuncLit{
			Type: &ast.FuncType{Params: &ast.FieldList{}},
			Body: &ast.BlockStmt{List: body},
		}),
	}
}

func (r *exprRewriter) addConversionEvents(call *ast.CallExpr, sym string, siteID string) {
	bytesExpr, ok := conversionBytesExpr(call, sym)
	if !ok {
		return
	}
	op := "C." + sym
	bytesAsInt := callExpr(ident("int"), bytesExpr)
	*r.pre = append(*r.pre, exprStmt(callExpr(sel(ident("prof"), "Conversion"), stringLit(siteID), stringLit(op), bytesAsInt)))
	if sym == "CString" || sym == "CBytes" {
		*r.pre = append(*r.pre, exprStmt(callExpr(sel(ident("prof"), "Memory"), stringLit(siteID), stringLit("malloc"), bytesAsInt)))
	}
	r.inst.needProf = true
}

func (r *exprRewriter) addAllocationEvents(call *ast.CallExpr, sym string, siteID string) {
	bytesExpr, ok := allocationBytesExpr(call, sym)
	if !ok {
		return
	}
	*r.pre = append(*r.pre, exprStmt(callExpr(sel(ident("prof"), "Memory"), stringLit(siteID), stringLit("malloc"), callExpr(ident("int"), bytesExpr))))
	r.inst.needProf = true
}

func (r *exprRewriter) addPointerCheck(call *ast.CallExpr, siteID string) {
	r.addPointerCheckTo(r.pre, call, siteID)
}

func (r *exprRewriter) addPointerCheckTo(stmts *[]ast.Stmt, call *ast.CallExpr, siteID string) {
	if !containsGoPointerSignal(call, r.inst.cAllocatedVar, r.inst.pointerVars, r.inst.summaries) {
		return
	}
	*stmts = append(*stmts, exprStmt(callExpr(sel(ident("prof"), "PointerCheck"), stringLit(siteID), intLit(50))))
	r.inst.needProf = true
}

func (r *exprRewriter) freeSiteID(call *ast.CallExpr, fallback string) string {
	if len(call.Args) != 1 {
		return fallback
	}
	if name, ok := unwrapPointerIdent(call.Args[0]); ok {
		if siteID, ok := r.inst.cAllocatedVar[name]; ok {
			if siteID != "__summary_cptr" {
				return siteID
			}
		}
	}
	return fallback
}

func cCallSymbol(call *ast.CallExpr) (string, bool) {
	selExpr, ok := call.Fun.(*ast.SelectorExpr)
	if !ok {
		return "", false
	}
	if id, ok := selExpr.X.(*ast.Ident); ok && id.Name == "C" {
		return selExpr.Sel.Name, true
	}
	return "", false
}

func conversionBytesExpr(call *ast.CallExpr, sym string) (ast.Expr, bool) {
	switch sym {
	case "CString":
		if len(call.Args) != 1 {
			return nil, false
		}
		return binaryExpr(callExpr(ident("len"), call.Args[0]), token.ADD, intLit(1)), true
	case "CBytes":
		if len(call.Args) != 1 {
			return nil, false
		}
		return callExpr(ident("len"), call.Args[0]), true
	case "GoString":
		return intLit(0), true
	case "GoStringN", "GoBytes":
		if len(call.Args) < 2 {
			return nil, false
		}
		return call.Args[1], true
	default:
		return nil, false
	}
}

func allocationBytesExpr(call *ast.CallExpr, sym string) (ast.Expr, bool) {
	switch sym {
	case "malloc":
		if len(call.Args) < 1 {
			return nil, false
		}
		return call.Args[0], true
	case "calloc":
		if len(call.Args) < 2 {
			return nil, false
		}
		return binaryExpr(call.Args[0], token.MUL, call.Args[1]), true
	case "realloc":
		if len(call.Args) < 2 {
			return nil, false
		}
		return call.Args[1], true
	default:
		return nil, false
	}
}

func isCAllocationSymbol(sym string) bool {
	return sym == "CString" || sym == "CBytes" || sym == "malloc" || sym == "calloc" || sym == "realloc"
}

func containsGoPointerSignal(node ast.Node, cAllocatedVars map[string]string, pointerVars map[string]bool, summaries map[string]funcSummary) bool {
	found := false
	ast.Inspect(node, func(n ast.Node) bool {
		if found {
			return false
		}
		if pointerOriginOfNode(n, cAllocatedVars, pointerVars, summaries) == originGo {
			found = true
			return false
		}
		return true
	})
	return found
}

func pointerOriginOfNode(node ast.Node, cAllocatedVars map[string]string, pointerVars map[string]bool, summaries map[string]funcSummary) pointerOrigin {
	expr, ok := node.(ast.Expr)
	if !ok {
		return originUnknown
	}
	return pointerOriginOf(expr, cAllocatedVars, pointerVars, summaries, "")
}

func pointerOriginOf(expr ast.Expr, cAllocatedVars map[string]string, pointerVars map[string]bool, summaries map[string]funcSummary, pkgKey string) pointerOrigin {
	switch e := expr.(type) {
	case *ast.CallExpr:
		if isUnsafePointerCall(e) && len(e.Args) == 1 {
			return pointerOriginOf(e.Args[0], cAllocatedVars, pointerVars, summaries, pkgKey)
		}
		if sym, ok := cCallSymbol(e); ok && isCAllocationSymbol(sym) {
			return originC
		}
		if summary, ok := callSummary(e, summaries); ok {
			if summary.returnGoPointer {
				return originGo
			}
			if summary.returnCPointer {
				return originC
			}
			if summary.returnParam >= 0 && summary.returnParam < len(e.Args) {
				return pointerOriginOf(e.Args[summary.returnParam], cAllocatedVars, pointerVars, summaries, pkgKey)
			}
		}
		return originUnknown
	case *ast.UnaryExpr:
		if e.Op == token.AND {
			return originGo
		}
		return pointerOriginOf(e.X, cAllocatedVars, pointerVars, summaries, pkgKey)
	case *ast.Ident:
		if _, ok := cAllocatedVars[e.Name]; ok {
			return originC
		}
		if pointerVars[e.Name] {
			return originGo
		}
		return originUnknown
	case *ast.StarExpr:
		return pointerOriginOf(e.X, cAllocatedVars, pointerVars, summaries, pkgKey)
	case *ast.ParenExpr:
		return pointerOriginOf(e.X, cAllocatedVars, pointerVars, summaries, pkgKey)
	default:
		return originUnknown
	}
}

func callSummary(call *ast.CallExpr, summaries map[string]funcSummary) (funcSummary, bool) {
	switch fun := call.Fun.(type) {
	case *ast.Ident:
		summary, ok := summaries[fun.Name]
		return summary, ok
	case *ast.SelectorExpr:
		summary, ok := summaries[fun.Sel.Name]
		return summary, ok
	default:
		return funcSummary{}, false
	}
}

func isUnsafePointerCall(call *ast.CallExpr) bool {
	if len(call.Args) != 1 {
		return false
	}
	if selExpr, ok := call.Fun.(*ast.SelectorExpr); ok {
		if id, ok := selExpr.X.(*ast.Ident); ok && id.Name == "unsafe" && selExpr.Sel.Name == "Pointer" {
			return true
		}
	}
	return false
}

func unwrapPointerIdent(expr ast.Expr) (string, bool) {
	switch e := expr.(type) {
	case *ast.Ident:
		return e.Name, true
	case *ast.CallExpr:
		if isUnsafePointerCall(e) {
			return unwrapPointerIdent(e.Args[0])
		}
	case *ast.ParenExpr:
		return unwrapPointerIdent(e.X)
	}
	return "", false
}

func assignedIdentName(expr ast.Expr) (string, bool) {
	if ident, ok := expr.(*ast.Ident); ok && ident.Name != "_" {
		return ident.Name, true
	}
	return "", false
}

func exportDirectiveName(group *ast.CommentGroup) (string, bool) {
	if group == nil {
		return "", false
	}
	for _, comment := range group.List {
		text := strings.TrimSpace(strings.TrimPrefix(comment.Text, "//"))
		fields := strings.Fields(text)
		if len(fields) == 2 && fields[0] == "export" {
			return fields[1], true
		}
	}
	return "", false
}

func repairExportDirectives(text string, exportedNames map[string]bool) string {
	lines := strings.Split(text, "\n")
	cleaned := make([]string, 0, len(lines))
	for _, line := range lines {
		fields := strings.Fields(strings.TrimSpace(line))
		if len(fields) == 2 && fields[0] == "//export" && exportedNames[fields[1]] {
			continue
		}
		cleaned = append(cleaned, line)
	}
	repaired := make([]string, 0, len(cleaned)+len(exportedNames))
	for _, line := range cleaned {
		if name, ok := exportedFuncName(line); ok && exportedNames[name] {
			if len(repaired) > 0 && strings.TrimSpace(repaired[len(repaired)-1]) != "" {
				repaired = append(repaired, "")
			}
			repaired = append(repaired, "//export "+name)
		}
		repaired = append(repaired, line)
	}
	return strings.Join(repaired, "\n")
}

func exportedFuncName(line string) (string, bool) {
	trimmed := strings.TrimSpace(line)
	if !strings.HasPrefix(trimmed, "func ") {
		return "", false
	}
	rest := strings.TrimPrefix(trimmed, "func ")
	idx := strings.Index(rest, "(")
	if idx <= 0 {
		return "", false
	}
	name := rest[:idx]
	if strings.ContainsAny(name, " \t") {
		return "", false
	}
	return name, true
}

func addNamedImport(file *ast.File, name string, path string) {
	for _, imp := range file.Imports {
		if strings.Trim(imp.Path.Value, `"`) == path {
			return
		}
	}
	spec := &ast.ImportSpec{Path: stringLit(path)}
	if name != "" {
		spec.Name = ident(name)
	}
	for _, decl := range file.Decls {
		gen, ok := decl.(*ast.GenDecl)
		if !ok || gen.Tok != token.IMPORT {
			continue
		}
		hasC := false
		for _, existing := range gen.Specs {
			if imp, ok := existing.(*ast.ImportSpec); ok && strings.Trim(imp.Path.Value, `"`) == "C" {
				hasC = true
				break
			}
		}
		if hasC {
			continue
		}
		gen.Specs = append(gen.Specs, spec)
		file.Imports = append(file.Imports, spec)
		return
	}
	decl := &ast.GenDecl{Tok: token.IMPORT, Specs: []ast.Spec{spec}}
	insertAt := 0
	for idx, existing := range file.Decls {
		gen, ok := existing.(*ast.GenDecl)
		if !ok || gen.Tok != token.IMPORT {
			break
		}
		insertAt = idx + 1
	}
	file.Decls = append(file.Decls, nil)
	copy(file.Decls[insertAt+1:], file.Decls[insertAt:])
	file.Decls[insertAt] = decl
	file.Imports = append(file.Imports, spec)
}

func updateGoMod(root string, runtimePath string) error {
	path := filepath.Join(root, "go.mod")
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	text := string(data)
	var b strings.Builder
	b.WriteString(strings.TrimRight(text, "\n"))
	if !strings.Contains(text, runtimeImportPath) {
		b.WriteString("\n\nrequire ")
		b.WriteString(runtimeImportPath)
		b.WriteString(" v0.0.0")
	}
	if !strings.Contains(text, "replace "+runtimeImportPath) {
		b.WriteString("\n\nreplace ")
		b.WriteString(runtimeImportPath)
		b.WriteString(" => ")
		b.WriteString(filepath.ToSlash(runtimePath))
	}
	b.WriteString("\n")
	return os.WriteFile(path, []byte(b.String()), 0o644)
}

func (i *instrumenter) siteID(pos token.Pos, symbol string) string {
	location := i.fset.Position(pos)
	key := fmt.Sprintf("%s:%d:%d:%s", i.relPath, location.Line, location.Column, symbol)
	sum := sha1.Sum([]byte(key))
	return hex.EncodeToString(sum[:])[:10]
}

func (i *instrumenter) nextTemp(prefix string) string {
	i.tempIndex++
	return fmt.Sprintf("__cgoprof_%s_%d", prefix, i.tempIndex)
}

func ident(name string) *ast.Ident {
	return ast.NewIdent(name)
}

func sel(x ast.Expr, name string) *ast.SelectorExpr {
	return &ast.SelectorExpr{X: x, Sel: ident(name)}
}

func callExpr(fun ast.Expr, args ...ast.Expr) *ast.CallExpr {
	return &ast.CallExpr{Fun: fun, Args: args}
}

func assignStmt(name string, expr ast.Expr) *ast.AssignStmt {
	return &ast.AssignStmt{Lhs: []ast.Expr{ident(name)}, Tok: token.DEFINE, Rhs: []ast.Expr{expr}}
}

func exprStmt(expr ast.Expr) *ast.ExprStmt {
	return &ast.ExprStmt{X: expr}
}

func stringLit(value string) *ast.BasicLit {
	return &ast.BasicLit{Kind: token.STRING, Value: strconv.Quote(value)}
}

func intLit(value int) *ast.BasicLit {
	return &ast.BasicLit{Kind: token.INT, Value: strconv.Itoa(value)}
}

func binaryExpr(left ast.Expr, op token.Token, right ast.Expr) *ast.BinaryExpr {
	return &ast.BinaryExpr{X: left, Op: op, Y: right}
}
