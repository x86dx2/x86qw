// Package zdeflate is a faithful port of zlib 1.2.12's deflate at level 6
// (deflate_slow, windowBits 15, memLevel 8, default strategy). Its raw
// output is byte-identical to CPython's
// zlib.compressobj(6, zlib.DEFLATED, -15).compress(data) + flush(), which
// is what zipfile uses for ZIP_DEFLATED members. Go's compress/flate makes
// different choices, so the published package digests can only be
// reproduced with this port.
package zdeflate

const (
	lengthCodes = 29
	literals    = 256
	lCodes      = literals + 1 + lengthCodes
	dCodes      = 30
	blCodes     = 19
	heapSize    = 2*lCodes + 1
	maxBits     = 15
	maxBlBits   = 7
	endBlock    = 256
	rep3to6     = 16
	repz3to10   = 17
	repz11to138 = 18

	storedBlock = 0
	staticTrees = 1
	dynTrees    = 2

	minMatch     = 3
	maxMatch     = 258
	minLookahead = maxMatch + minMatch + 1

	wBits      = 15
	wSize      = 1 << wBits
	wMask      = wSize - 1
	windowSize = 2 * wSize
	maxDist    = wSize - minLookahead
	hashBits   = 8 + 7 // memLevel 8
	hashSize   = 1 << hashBits
	hashMask   = hashSize - 1
	hashShift  = (hashBits + minMatch - 1) / minMatch
	tooFar     = 4096
	litBufsize = 1 << (8 + 6)
	symEnd     = (litBufsize - 1) * 3

	// configuration_table[6]
	goodMatch    = 8
	maxLazyMatch = 16
	niceMatch    = 128
	maxChain     = 128
)

var (
	extraLbits  = [lengthCodes]int{0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0}
	extraDbits  = [dCodes]int{0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13}
	extraBlbits = [blCodes]int{0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 3, 7}
	blOrder     = [blCodes]int{16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15}

	staticLtree [lCodes + 2]ct
	staticDtree [dCodes]ct
	distCode    [512]uint8
	lengthCode  [maxMatch - minMatch + 1]uint8
	baseLength  [lengthCodes]int
	baseDist    [dCodes]int
)

// ct is zlib's ct_data: fc is Freq/Code and dl is Dad/Len (C unions).
type ct struct{ fc, dl uint16 }

type staticDesc struct {
	tree      []ct
	extra     []int
	extraBase int
	elems     int
	maxLength int
}

type treeDesc struct {
	tree    []ct
	maxCode int
	stat    *staticDesc
}

var (
	staticL  = staticDesc{staticLtree[:], extraLbits[:], literals + 1, lCodes, maxBits}
	staticD  = staticDesc{staticDtree[:], extraDbits[:], 0, dCodes, maxBits}
	staticBl = staticDesc{nil, extraBlbits[:], 0, blCodes, maxBlBits}
)

func init() {
	length := 0
	code := 0
	for code = 0; code < lengthCodes-1; code++ {
		baseLength[code] = length
		for n := 0; n < 1<<extraLbits[code]; n++ {
			lengthCode[length] = uint8(code)
			length++
		}
	}
	lengthCode[length-1] = uint8(code)
	dist := 0
	for code = 0; code < 16; code++ {
		baseDist[code] = dist
		for n := 0; n < 1<<extraDbits[code]; n++ {
			distCode[dist] = uint8(code)
			dist++
		}
	}
	dist >>= 7
	for ; code < dCodes; code++ {
		baseDist[code] = dist << 7
		for n := 0; n < 1<<(extraDbits[code]-7); n++ {
			distCode[256+dist] = uint8(code)
			dist++
		}
	}
	var blCount [maxBits + 1]uint16
	n := 0
	for ; n <= 143; n++ {
		staticLtree[n].dl = 8
		blCount[8]++
	}
	for ; n <= 255; n++ {
		staticLtree[n].dl = 9
		blCount[9]++
	}
	for ; n <= 279; n++ {
		staticLtree[n].dl = 7
		blCount[7]++
	}
	for ; n <= 287; n++ {
		staticLtree[n].dl = 8
		blCount[8]++
	}
	genCodes(staticLtree[:], lCodes+1, blCount[:])
	for n := 0; n < dCodes; n++ {
		staticDtree[n].dl = 5
		staticDtree[n].fc = uint16(biReverse(uint(n), 5))
	}
}

func dCode(dist int) int {
	if dist < 256 {
		return int(distCode[dist])
	}
	return int(distCode[256+(dist>>7)])
}

func biReverse(code uint, length int) uint {
	var res uint
	for {
		res |= code & 1
		code >>= 1
		res <<= 1
		length--
		if length <= 0 {
			break
		}
	}
	return res >> 1
}

func genCodes(tree []ct, maxCode int, blCount []uint16) {
	var nextCode [maxBits + 1]uint16
	code := uint(0)
	for bits := 1; bits <= maxBits; bits++ {
		code = (code + uint(blCount[bits-1])) << 1
		nextCode[bits] = uint16(code)
	}
	for n := 0; n <= maxCode; n++ {
		length := int(tree[n].dl)
		if length == 0 {
			continue
		}
		tree[n].fc = uint16(biReverse(uint(nextCode[length]), length))
		nextCode[length]++
	}
}

type state struct {
	input []byte
	inPos int

	window []byte
	prev   []uint16
	head   []uint16
	insH   uint

	strstart, lookahead     int
	matchStart, prevMatch   int
	matchLength, prevLength int
	matchAvailable          bool
	blockStart              int
	insert                  int

	dynLtree [heapSize]ct
	dynDtree [2*dCodes + 1]ct
	blTree   [2*blCodes + 1]ct
	lDesc    treeDesc
	dDesc    treeDesc
	blDesc   treeDesc
	blCount  [maxBits + 1]uint16
	heap     [2*lCodes + 1]int
	heapLen  int
	heapMax  int
	depth    [2*lCodes + 1]uint8

	symBuf  []byte
	symNext int
	optLen  uint64
	stLen   uint64

	out   []byte
	acc   uint64
	nbits uint
}

// Compress returns the raw deflate stream zlib 1.2.12 produces at level 6.
func Compress(data []byte) []byte {
	s := &state{
		input:  data,
		window: make([]byte, windowSize),
		prev:   make([]uint16, wSize),
		head:   make([]uint16, hashSize),
		symBuf: make([]byte, symEnd),
		out:    make([]byte, 0, len(data)/2+64),
	}
	s.lDesc = treeDesc{tree: s.dynLtree[:], stat: &staticL}
	s.dDesc = treeDesc{tree: s.dynDtree[:], stat: &staticD}
	s.blDesc = treeDesc{tree: s.blTree[:], stat: &staticBl}
	s.initBlock()
	s.matchLength, s.prevLength = minMatch-1, minMatch-1
	s.deflateSlow()
	return s.out
}

func (s *state) updateHash(h uint, c byte) uint { return ((h << hashShift) ^ uint(c)) & hashMask }

func (s *state) insertString(str int) int {
	s.insH = s.updateHash(s.insH, s.window[str+minMatch-1])
	matchHead := s.head[s.insH]
	s.prev[str&wMask] = matchHead
	s.head[s.insH] = uint16(str)
	return int(matchHead)
}

func (s *state) slideHash() {
	for i, m := range s.head {
		if int(m) >= wSize {
			s.head[i] = m - wSize
		} else {
			s.head[i] = 0
		}
	}
	for i, m := range s.prev {
		if int(m) >= wSize {
			s.prev[i] = m - wSize
		} else {
			s.prev[i] = 0
		}
	}
}

func (s *state) readBuf(start, size int) int {
	n := len(s.input) - s.inPos
	if n > size {
		n = size
	}
	if n <= 0 {
		return 0
	}
	copy(s.window[start:start+n], s.input[s.inPos:s.inPos+n])
	s.inPos += n
	return n
}

func (s *state) fillWindow() {
	for {
		more := windowSize - s.lookahead - s.strstart
		if s.strstart >= wSize+maxDist {
			copy(s.window[:wSize-more], s.window[wSize:wSize+wSize-more])
			s.matchStart -= wSize
			s.strstart -= wSize
			s.blockStart -= wSize
			if s.insert > s.strstart {
				s.insert = s.strstart
			}
			s.slideHash()
			more += wSize
		}
		if s.inPos >= len(s.input) {
			break
		}
		n := s.readBuf(s.strstart+s.lookahead, more)
		s.lookahead += n
		if s.lookahead+s.insert >= minMatch {
			str := s.strstart - s.insert
			s.insH = uint(s.window[str])
			s.insH = s.updateHash(s.insH, s.window[str+1])
			for s.insert > 0 {
				s.insH = s.updateHash(s.insH, s.window[str+minMatch-1])
				s.prev[str&wMask] = s.head[s.insH]
				s.head[s.insH] = uint16(str)
				str++
				s.insert--
				if s.lookahead+s.insert < minMatch {
					break
				}
			}
		}
		if !(s.lookahead < minLookahead && s.inPos < len(s.input)) {
			break
		}
	}
}

func (s *state) longestMatch(curMatch int) int {
	chainLength := maxChain
	scan := s.strstart
	bestLen := s.prevLength
	nice := niceMatch
	limit := 0
	if s.strstart > maxDist {
		limit = s.strstart - maxDist
	}
	window := s.window
	strend := s.strstart + maxMatch
	scanEnd1 := window[scan+bestLen-1]
	scanEnd := window[scan+bestLen]
	if s.prevLength >= goodMatch {
		chainLength >>= 2
	}
	if nice > s.lookahead {
		nice = s.lookahead
	}
	for {
		match := curMatch
		if window[match+bestLen] == scanEnd && window[match+bestLen-1] == scanEnd1 &&
			window[match] == window[scan] && window[match+1] == window[scan+1] {
			sp, mp := scan+2, match+2
			for {
				equal := true
				for i := 0; i < 8; i++ {
					sp++
					mp++
					if window[sp] != window[mp] {
						equal = false
						break
					}
				}
				if !equal || sp >= strend {
					break
				}
			}
			length := maxMatch - (strend - sp)
			if length > bestLen {
				s.matchStart = curMatch
				bestLen = length
				if length >= nice {
					break
				}
				scanEnd1 = window[scan+bestLen-1]
				scanEnd = window[scan+bestLen]
			}
		}
		curMatch = int(s.prev[curMatch&wMask])
		if curMatch <= limit {
			break
		}
		chainLength--
		if chainLength == 0 {
			break
		}
	}
	if bestLen <= s.lookahead {
		return bestLen
	}
	return s.lookahead
}

func (s *state) tallyLit(c byte) bool {
	s.symBuf[s.symNext] = 0
	s.symBuf[s.symNext+1] = 0
	s.symBuf[s.symNext+2] = c
	s.symNext += 3
	s.dynLtree[c].fc++
	return s.symNext == symEnd
}

func (s *state) tallyDist(dist, length int) bool {
	s.symBuf[s.symNext] = byte(dist)
	s.symBuf[s.symNext+1] = byte(dist >> 8)
	s.symBuf[s.symNext+2] = byte(length)
	s.symNext += 3
	dist--
	s.dynLtree[int(lengthCode[length])+literals+1].fc++
	s.dynDtree[dCode(dist)].fc++
	return s.symNext == symEnd
}

func (s *state) flushBlock(last bool) {
	var buf []byte
	if s.blockStart >= 0 {
		buf = s.window[s.blockStart:]
	}
	s.trFlushBlock(buf, uint64(s.strstart-s.blockStart), last)
	s.blockStart = s.strstart
}

func (s *state) deflateSlow() {
	for {
		if s.lookahead < minLookahead {
			s.fillWindow()
			if s.lookahead == 0 {
				break
			}
		}
		hashHead := 0
		if s.lookahead >= minMatch {
			hashHead = s.insertString(s.strstart)
		}
		s.prevLength, s.prevMatch = s.matchLength, s.matchStart
		s.matchLength = minMatch - 1
		if hashHead != 0 && s.prevLength < maxLazyMatch && s.strstart-hashHead <= maxDist {
			s.matchLength = s.longestMatch(hashHead)
			if s.matchLength <= 5 && s.matchLength == minMatch && s.strstart-s.matchStart > tooFar {
				s.matchLength = minMatch - 1
			}
		}
		if s.prevLength >= minMatch && s.matchLength <= s.prevLength {
			maxInsert := s.strstart + s.lookahead - minMatch
			bflush := s.tallyDist(s.strstart-1-s.prevMatch, s.prevLength-minMatch)
			s.lookahead -= s.prevLength - 1
			s.prevLength -= 2
			for {
				s.strstart++
				if s.strstart <= maxInsert {
					s.insertString(s.strstart)
				}
				s.prevLength--
				if s.prevLength == 0 {
					break
				}
			}
			s.matchAvailable = false
			s.matchLength = minMatch - 1
			s.strstart++
			if bflush {
				s.flushBlock(false)
			}
		} else if s.matchAvailable {
			if s.tallyLit(s.window[s.strstart-1]) {
				s.flushBlock(false)
			}
			s.strstart++
			s.lookahead--
		} else {
			s.matchAvailable = true
			s.strstart++
			s.lookahead--
		}
	}
	if s.matchAvailable {
		s.tallyLit(s.window[s.strstart-1])
		s.matchAvailable = false
	}
	s.insert = min(s.strstart, minMatch-1)
	s.flushBlock(true)
}

// Bit output (LSB first, identical stream to send_bits/bi_windup).

func (s *state) sendBits(value, length int) {
	s.acc |= uint64(value) << s.nbits
	s.nbits += uint(length)
	for s.nbits >= 8 {
		s.out = append(s.out, byte(s.acc))
		s.acc >>= 8
		s.nbits -= 8
	}
}

func (s *state) windup() {
	if s.nbits > 0 {
		s.out = append(s.out, byte(s.acc))
	}
	s.acc, s.nbits = 0, 0
}

func (s *state) sendCode(c int, tree []ct) { s.sendBits(int(tree[c].fc), int(tree[c].dl)) }

func (s *state) initBlock() {
	for n := 0; n < lCodes; n++ {
		s.dynLtree[n].fc = 0
	}
	for n := 0; n < dCodes; n++ {
		s.dynDtree[n].fc = 0
	}
	for n := 0; n < blCodes; n++ {
		s.blTree[n].fc = 0
	}
	s.dynLtree[endBlock].fc = 1
	s.optLen, s.stLen = 0, 0
	s.symNext = 0
}

func smaller(tree []ct, n, m int, depth []uint8) bool {
	return tree[n].fc < tree[m].fc || (tree[n].fc == tree[m].fc && depth[n] <= depth[m])
}

func (s *state) pqdownheap(tree []ct, k int) {
	v := s.heap[k]
	j := k << 1
	for j <= s.heapLen {
		if j < s.heapLen && smaller(tree, s.heap[j+1], s.heap[j], s.depth[:]) {
			j++
		}
		if smaller(tree, v, s.heap[j], s.depth[:]) {
			break
		}
		s.heap[k] = s.heap[j]
		k = j
		j <<= 1
	}
	s.heap[k] = v
}

func (s *state) genBitlen(desc *treeDesc) {
	tree := desc.tree
	maxCode := desc.maxCode
	stree := desc.stat.tree
	extra := desc.stat.extra
	base := desc.stat.extraBase
	maxLength := desc.stat.maxLength
	overflow := 0
	for bits := 0; bits <= maxBits; bits++ {
		s.blCount[bits] = 0
	}
	tree[s.heap[s.heapMax]].dl = 0
	h := s.heapMax + 1
	for ; h < heapSize; h++ {
		n := s.heap[h]
		bits := int(tree[tree[n].dl].dl) + 1
		if bits > maxLength {
			bits = maxLength
			overflow++
		}
		tree[n].dl = uint16(bits)
		if n > maxCode {
			continue
		}
		s.blCount[bits]++
		xbits := 0
		if n >= base {
			xbits = extra[n-base]
		}
		f := uint64(tree[n].fc)
		s.optLen += f * uint64(bits+xbits)
		if stree != nil {
			s.stLen += f * uint64(int(stree[n].dl)+xbits)
		}
	}
	if overflow == 0 {
		return
	}
	for {
		bits := maxLength - 1
		for s.blCount[bits] == 0 {
			bits--
		}
		s.blCount[bits]--
		s.blCount[bits+1] += 2
		s.blCount[maxLength]--
		overflow -= 2
		if overflow <= 0 {
			break
		}
	}
	for bits := maxLength; bits != 0; bits-- {
		n := int(s.blCount[bits])
		for n != 0 {
			h--
			m := s.heap[h]
			if m > maxCode {
				continue
			}
			if int(tree[m].dl) != bits {
				s.optLen += uint64(bits-int(tree[m].dl)) * uint64(tree[m].fc)
				tree[m].dl = uint16(bits)
			}
			n--
		}
	}
}

func (s *state) buildTree(desc *treeDesc) {
	tree := desc.tree
	stree := desc.stat.tree
	elems := desc.stat.elems
	maxCode := -1
	s.heapLen, s.heapMax = 0, heapSize
	for n := 0; n < elems; n++ {
		if tree[n].fc != 0 {
			s.heapLen++
			s.heap[s.heapLen] = n
			maxCode = n
			s.depth[n] = 0
		} else {
			tree[n].dl = 0
		}
	}
	for s.heapLen < 2 {
		node := 0
		if maxCode < 2 {
			maxCode++
			node = maxCode
		}
		s.heapLen++
		s.heap[s.heapLen] = node
		tree[node].fc = 1
		s.depth[node] = 0
		s.optLen--
		if stree != nil {
			s.stLen -= uint64(stree[node].dl)
		}
	}
	desc.maxCode = maxCode
	for n := s.heapLen / 2; n >= 1; n-- {
		s.pqdownheap(tree, n)
	}
	node := elems
	for {
		n := s.heap[1]
		s.heap[1] = s.heap[s.heapLen]
		s.heapLen--
		s.pqdownheap(tree, 1)
		m := s.heap[1]
		s.heapMax--
		s.heap[s.heapMax] = n
		s.heapMax--
		s.heap[s.heapMax] = m
		tree[node].fc = tree[n].fc + tree[m].fc
		depth := s.depth[n]
		if s.depth[m] > depth {
			depth = s.depth[m]
		}
		s.depth[node] = depth + 1
		tree[n].dl = uint16(node)
		tree[m].dl = uint16(node)
		s.heap[1] = node
		node++
		s.pqdownheap(tree, 1)
		if s.heapLen < 2 {
			break
		}
	}
	s.heapMax--
	s.heap[s.heapMax] = s.heap[1]
	s.genBitlen(desc)
	genCodes(tree, maxCode, s.blCount[:])
}

func (s *state) scanTree(tree []ct, maxCode int) {
	prevlen := -1
	nextlen := int(tree[0].dl)
	count := 0
	maxCount, minCount := 7, 4
	if nextlen == 0 {
		maxCount, minCount = 138, 3
	}
	tree[maxCode+1].dl = 0xffff
	for n := 0; n <= maxCode; n++ {
		curlen := nextlen
		nextlen = int(tree[n+1].dl)
		count++
		if count < maxCount && curlen == nextlen {
			continue
		} else if count < minCount {
			s.blTree[curlen].fc += uint16(count)
		} else if curlen != 0 {
			if curlen != prevlen {
				s.blTree[curlen].fc++
			}
			s.blTree[rep3to6].fc++
		} else if count <= 10 {
			s.blTree[repz3to10].fc++
		} else {
			s.blTree[repz11to138].fc++
		}
		count = 0
		prevlen = curlen
		switch {
		case nextlen == 0:
			maxCount, minCount = 138, 3
		case curlen == nextlen:
			maxCount, minCount = 6, 3
		default:
			maxCount, minCount = 7, 4
		}
	}
}

func (s *state) sendTree(tree []ct, maxCode int) {
	prevlen := -1
	nextlen := int(tree[0].dl)
	count := 0
	maxCount, minCount := 7, 4
	if nextlen == 0 {
		maxCount, minCount = 138, 3
	}
	for n := 0; n <= maxCode; n++ {
		curlen := nextlen
		nextlen = int(tree[n+1].dl)
		count++
		if count < maxCount && curlen == nextlen {
			continue
		} else if count < minCount {
			for {
				s.sendCode(curlen, s.blTree[:])
				count--
				if count == 0 {
					break
				}
			}
		} else if curlen != 0 {
			if curlen != prevlen {
				s.sendCode(curlen, s.blTree[:])
				count--
			}
			s.sendCode(rep3to6, s.blTree[:])
			s.sendBits(count-3, 2)
		} else if count <= 10 {
			s.sendCode(repz3to10, s.blTree[:])
			s.sendBits(count-3, 3)
		} else {
			s.sendCode(repz11to138, s.blTree[:])
			s.sendBits(count-11, 7)
		}
		count = 0
		prevlen = curlen
		switch {
		case nextlen == 0:
			maxCount, minCount = 138, 3
		case curlen == nextlen:
			maxCount, minCount = 6, 3
		default:
			maxCount, minCount = 7, 4
		}
	}
}

func (s *state) buildBlTree() int {
	s.scanTree(s.dynLtree[:], s.lDesc.maxCode)
	s.scanTree(s.dynDtree[:], s.dDesc.maxCode)
	s.buildTree(&s.blDesc)
	maxBlindex := blCodes - 1
	for ; maxBlindex >= 3; maxBlindex-- {
		if s.blTree[blOrder[maxBlindex]].dl != 0 {
			break
		}
	}
	s.optLen += 3*uint64(maxBlindex+1) + 5 + 5 + 4
	return maxBlindex
}

func (s *state) sendAllTrees(lcodes, dcodes, blcodes int) {
	s.sendBits(lcodes-257, 5)
	s.sendBits(dcodes-1, 5)
	s.sendBits(blcodes-4, 4)
	for rank := 0; rank < blcodes; rank++ {
		s.sendBits(int(s.blTree[blOrder[rank]].dl), 3)
	}
	s.sendTree(s.dynLtree[:], lcodes-1)
	s.sendTree(s.dynDtree[:], dcodes-1)
}

func (s *state) compressBlock(ltree, dtree []ct) {
	for sx := 0; sx < s.symNext; sx += 3 {
		dist := int(s.symBuf[sx]) | int(s.symBuf[sx+1])<<8
		lc := int(s.symBuf[sx+2])
		if dist == 0 {
			s.sendCode(lc, ltree)
			continue
		}
		code := int(lengthCode[lc])
		s.sendCode(code+literals+1, ltree)
		if extra := extraLbits[code]; extra != 0 {
			s.sendBits(lc-baseLength[code], extra)
		}
		dist--
		code = dCode(dist)
		s.sendCode(code, dtree)
		if extra := extraDbits[code]; extra != 0 {
			s.sendBits(dist-baseDist[code], extra)
		}
	}
	s.sendCode(endBlock, ltree)
}

func boolBit(value bool) int {
	if value {
		return 1
	}
	return 0
}

func (s *state) trFlushBlock(buf []byte, storedLen uint64, last bool) {
	s.buildTree(&s.lDesc)
	s.buildTree(&s.dDesc)
	maxBlindex := s.buildBlTree()
	optLenb := (s.optLen + 3 + 7) >> 3
	staticLenb := (s.stLen + 3 + 7) >> 3
	if staticLenb <= optLenb {
		optLenb = staticLenb
	}
	switch {
	case storedLen+4 <= optLenb && buf != nil:
		s.sendBits(storedBlock<<1+boolBit(last), 3)
		s.windup()
		s.out = append(s.out, byte(storedLen), byte(storedLen>>8), byte(^storedLen), byte(^storedLen>>8))
		s.out = append(s.out, buf[:storedLen]...)
	case staticLenb == optLenb:
		s.sendBits(staticTrees<<1+boolBit(last), 3)
		s.compressBlock(staticLtree[:], staticDtree[:])
	default:
		s.sendBits(dynTrees<<1+boolBit(last), 3)
		s.sendAllTrees(s.lDesc.maxCode+1, s.dDesc.maxCode+1, maxBlindex+1)
		s.compressBlock(s.dynLtree[:], s.dynDtree[:])
	}
	s.initBlock()
	if last {
		s.windup()
	}
}
