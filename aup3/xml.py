''' Audacity binary XML serializer / deserializer '''

import struct
from typing import Union, Literal, Iterable, Iterator, get_args, NamedTuple
from dataclasses import dataclass, field
from itertools import tee
from io import BytesIO

lookahead = lambda it, default: next(tee(it, 1)[0], default)

__all__ = [
	'Attribute', 'Token', 'TokenSerializer',
	'Element', 'TextNode', 'Node', 'NodeDeserializer', 'NodeSerializer',
	'decode', 'encode', 'get_root',
]

# low-level token stream (lossless)

NameID = int # u16

Attribute = Union[
	tuple[Literal['String'], str],
	tuple[Literal['Int'], int], # i32
	tuple[Literal['Bool'], bool],
	tuple[Literal['Long'], int], # i32
	tuple[Literal['LongLong'], int], # i64
	tuple[Literal['SizeT'], int], # u32
	# for float/double there's an extra 'digits' i32
	tuple[Literal['Float'], float, int],
	tuple[Literal['Double'], float, int],
]

Token = Union[
	tuple[Literal['CharSize'], int], # u8
	tuple[Literal['StartTag'], NameID],
	tuple[Literal['EndTag'], NameID],
	tuple[Literal['Attr'], NameID, Attribute],
	tuple[Literal['Data'], str],
	tuple[Literal['Raw'], str],
	tuple[Literal['Push']],
	tuple[Literal['Pop']],
	tuple[Literal['Name'], NameID, str],
]

def read_fixed(f, n):
	data = f.read(n)
	assert len(data) == n, f'expected {n} bytes, got {len(data)}'
	return data

read_primitive = lambda f, fmt: \
	struct.unpack('<' + fmt, f.read(struct.Struct(fmt).size))[0]
write_primitive = lambda value, fmt: \
	struct.pack('<' + fmt, value)

read_bytes = lambda f, wide=True: \
	read_fixed(f, read_primitive(f, 'I' if wide else 'H'))
write_bytes = lambda value, wide=True: \
	[write_primitive(len(value), 'I' if wide else 'H'), value]

encodings = {1: 'utf-8', 2: 'utf-16le', 4: 'utf-32le'}
read_string = lambda f, nchars, wide=True: \
	read_bytes(f, wide=wide).decode(encodings[nchars])
write_string = lambda value, nchars, wide=True: \
	write_bytes(value.encode(encodings[nchars]), wide=wide)

read_bool = lambda f: {0: False, 1: True}[f.read(1)[0]]
write_bool = lambda value: [ bytes([ int(value) ]) ]

attr_names = [ get_args(get_args(t)[0])[0] for t in get_args(Attribute) ]
opcode_names = [ get_args(get_args(t)[0])[0] for t in get_args(Token) ]
opcode_names = sum(( attr_names if name == 'Attr' else [name] for name in opcode_names ), [])
name_opcodes = { name: ftype for ftype, name in enumerate(opcode_names) }

serializers = [
	'B', None, None,
	'string', 'i', (read_bool, write_bool), 'i', 'q', 'I', 'f', 'd',
	'string', 'string', None, None, 'string',
]

@dataclass
class TokenSerializer:
	nchars = -1

	def handle_token(self, token: Token):
		if token[0] == 'CharSize':
			assert token[1] in {1, 2, 4}, f'invalid char size {token[1]}'
			self.nchars = token[1]

	def read_one(self, f) -> Token:
		ftype = next(iter(f.read(1)))
		token = opcode_names[ftype],

		if 1 <= ftype < 11 or ftype == 15:
			token += read_primitive(f, 'H'),

		if handler := serializers[ftype]:
			if handler == 'string':
				token += read_string(f, self.nchars, ftype != 15),
			elif type(handler) is str:
				token += read_primitive(f, handler),
			else:
				token += handler[0](f),

		if 9 <= ftype < 11:
			token += read_primitive(f, 'i'),

		if 3 <= ftype < 11:
			token = 'Attr', token[1], (token[0], *token[2:]) # type: ignore
		self.handle_token(token)
		return token

	def write_one(self, token: Token) -> Iterable[bytes]:
		self.handle_token(token)
		if token[0] == 'Attr':
			token = token[2][0], token[1], *token[2][1:] # type: ignore

		name, *token = token # type: ignore
		ftype = name_opcodes[name]
		yield write_primitive(ftype, 'B')

		if 1 <= ftype < 11 or ftype == 15:
			fid, *token = token # type: ignore
			yield write_primitive(fid, 'H')

		if handler := serializers[ftype]:
			value, *token = token # type: ignore
			if handler == 'string':
				yield from write_string(value, self.nchars, ftype != 15)
			elif type(handler) is str:
				yield write_primitive(value, handler)
			else:
				yield from handler[1](value)

		if 9 <= ftype < 11:
			unk, *token = token # type: ignore
			yield write_primitive(unk, 'i')

		assert not token

	def read(self, f) -> Iterable[Token]:
		while True:
			try:
				yield self.read_one(f)
			except StopIteration:
				break

	def write(self, tokens: Iterable[Token]) -> Iterable[bytes]:
		for token in tokens:
			yield from self.write_one(token)

# high-level (does interning, tree structure)

class Element(NamedTuple):
	tag: str
	attrs: dict[str, Attribute]
	children: list['Node']

	def __repr__(self, prefix='') -> str:
		children = '\n' + ''.join(
			(c.__repr__(prefix + '    ') if type(c) is Element else repr(c)) + ',\n'
			for c in self.children) + prefix
		return prefix + f'Element({self.tag!r}, {self.attrs!r}, [{children if self.children else ""}])'

class TextNode(NamedTuple):
	is_raw: bool
	text: str

Node = Element | TextNode

@dataclass
class NodeDeserializer:
	names: dict[int, str] = field(default_factory=dict)

	def read_element(self, tag: str, rest: Iterator[Token]) -> Element:
		attrs = {}
		while (token := lookahead(rest, None)) and token[0] == 'Attr':
			next(rest)
			name = self.names[token[1]]
			assert name not in attrs
			attrs[name] = token[2]
		return Element(tag, attrs, list(self.read_nodes(rest, tag)))

	def read_one(self, token: Token, rest: Iterator[Token]) -> Node | None:
		if token[0] == 'Name':
			self.names[token[1]] = token[2]
		elif token[0] == 'Raw':
			return TextNode(True, token[1])
		elif token[0] == 'Data':
			return TextNode(False, token[1])
		elif token[0] == 'StartTag':
			return self.read_element(self.names[token[1]], rest)
		elif token[0] != 'CharSize':
			raise AssertionError(f'unexpected {token[0]} token')

	def read_nodes(self, tokens: Iterator[Token], tag=None) -> Iterable[Node]:
		for token in tokens:
			if token[0] == 'EndTag':
				assert self.names[token[1]] == tag, 'invalid end tag'
				return
			if node := self.read_one(token, tokens):
				yield node
		assert tag == None, 'missing end tag'

	def read(self, tokens: Iterable[Token]) -> Iterable[Node]:
		return self.read_nodes(tee(tokens, 1)[0])

@dataclass
class NodeSerializer:
	names: dict[str, int] = field(default_factory=dict)

	def write_name(self, name: str) -> Iterable[Token]:
		if self.names.setdefault(name, newid := len(self.names)) == newid:
			yield 'Name', newid, name

	def write_names(self, node: Node) -> Iterable[Token]:
		if isinstance(node, TextNode):
			return
		yield from self.write_name(node.tag)
		for key in node.attrs:
			yield from self.write_name(key)
		for child in node.children:
			yield from self.write_names(child)

	def write_doc(self, node: Node) -> Iterable[Token]:
		if isinstance(node, TextNode):
			yield ('Raw' if node.is_raw else 'Data'), node.text # type: ignore
			return
		yield 'StartTag', self.names[node.tag]
		for key, value in node.attrs.items():
			yield 'Attr', self.names[key], value
		for child in node.children:
			yield from self.write_doc(child)
		yield 'EndTag', self.names[node.tag]

# convenience API that combines the two layers

def decode(names: bytes, doc: bytes) -> list[Node]:
	td = TokenSerializer()
	nd = NodeDeserializer()
	nodes = list(nd.read(td.read(BytesIO(names))))
	assert not nodes, 'dictionary portion must not have content'
	return list(nd.read(td.read(BytesIO(doc))))

def concat(chunks: Iterable[bytes]) -> bytes:
	st = BytesIO()
	for chunk in chunks:
		st.write(chunk)
	return bytes(st.getbuffer())

def encode(nodes: list[Node]) -> tuple[bytes, bytes]:
	ts = TokenSerializer()
	ns = NodeSerializer()
	names = list(ts.write_one( ('CharSize', 4) ))
	for node in nodes:
		names += ts.write(ns.write_names(node))
	doc = []
	for node in nodes:
		doc += ts.write(ns.write_doc(node))
	return concat(names), concat(doc)

# add / remove doctype and xmlns

XMLNS = 'http://audacity.sourceforge.net/xml/'

def get_root(nodes: list[Node], xmlns=XMLNS) -> tuple[str, Element]:
	doctype = ''
	while nodes and isinstance(nodes[0], TextNode) and nodes[0].is_raw:
		doctype += nodes.pop(0).text  # type: ignore

	assert len(nodes) == 1
	root, = nodes
	assert isinstance(root, Element)
	root_xmlns = root.attrs.pop('xmlns', None)
	assert root_xmlns == ('String', xmlns), f'unexpected namespace {root_xmlns}'
	return doctype, root

def make_root(root: Element, doctype: str, xmlns=XMLNS) -> list[Node]:
	value = Element(root.tag, { **root.attrs, 'xmlns': ('String', xmlns) }, root.children)
	return [ TextNode(True, doctype), value ]
