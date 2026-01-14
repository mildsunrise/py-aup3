''' Audacity project schema unmarshaller '''

from . import xml
from dataclasses import dataclass, is_dataclass
from typing import Annotated, Optional, Literal, Union, get_origin, get_args
from types import UnionType, NoneType
from enum import IntEnum, unique
from collections import defaultdict

@dataclass(frozen=True)
class AudacityAttribute:
	kind: str

Long = Annotated[int, AudacityAttribute('Long')] # i32
LongLong = Annotated[int, AudacityAttribute('LongLong')] # i64
SizeT = Annotated[int, AudacityAttribute('SizeT')] # u32
Double = Annotated[float, AudacityAttribute('Double')]

_attribute_names = { 'String': str, 'Int': int, 'Bool': bool, 'Long': Long, 'LongLong': LongLong, 'SizeT': SizeT, 'Float': float, 'Double': Double }

class BaseElement: pass

ChildAttrModifier = Literal['one', 'many', 'optional']

class ElementSerializer:
	@classmethod
	def is_child_attr(cls, at: type) -> Optional[tuple[ChildAttrModifier, list[type]]]:
		origin, args = get_origin(at), get_args(at)
		if ct := cls.is_direct_child_attr(at):
			return 'one', ct
		if origin is list and len(args) == 1 and (ct := cls.is_direct_child_attr(args[0])):
			return 'many', ct
		if origin is Union and len(args) == 2 and args[1] is NoneType and (ct := cls.is_direct_child_attr(args[0])):
			return 'optional', ct

	@classmethod
	def is_direct_child_attr(cls, at: type) -> Optional[list[type]]:
		origin, args = get_origin(at), get_args(at)
		if type(at) is type and issubclass(at, BaseElement):
			return [at]
		if (origin is Union or origin is UnionType) and all(type(t) is type and issubclass(t, BaseElement) for t in args):
			return list(args)

	def __init__(self, element_cls: type):
		assert is_dataclass(element_cls) and issubclass(element_cls, BaseElement)
		self.cls = element_cls
		self.tag = element_cls.__name__.lower()
		self.children: dict[str, tuple[ChildAttrModifier, dict[type, ElementSerializer]]] = {}
		self.attrs = {}

		for key, atype in element_cls.__annotations__.items():
			if ct := self.is_child_attr(atype):
				tags = { t: self.get(t) for t in ct[1] }
				self.children[key] = ct[0], tags
				continue
			origin, args = get_origin(atype), get_args(atype)
			optional = False
			if origin is Union and len(args) == 2 and args[1] is NoneType:
				atype = args[0]
				optional = True
			kind = next((k for k, t in _attribute_names.items() if atype is t), None)
			assert kind, f'failed to parse attribute {key}: {atype}'
			self.attrs[key] = optional, kind

		children_by_tag = [ (t.tag, (t, k)) for k, (_, tags) in self.children.items() for t in tags.values() ]
		self.children_by_tag = dict(children_by_tag)
		assert len(children_by_tag) == len(children_by_tag)

	def deserialize(self, el: xml.Element, strict=True):
		assert el.tag == self.tag
		args = {}
		attrs = el.attrs.copy()
		for key, (optional, kind) in self.attrs.items():
			if value := attrs.pop(key, None):
				assert value[0] == kind, f'attribute {key} expected to be {kind}, got {value[0]}'
				args[key] = value[1]
			else:
				assert optional, f'required attribute {key} not present'
		assert (not strict) or (not attrs), f'unexpected attributes: {attrs}'

		children = defaultdict(list)
		for node in el.children:
			assert not isinstance(node, xml.TextNode)
			if entry := self.children_by_tag.get(node.tag):
				children[entry[1]].append(entry[0].deserialize(node, strict=strict))
			else:
				assert not strict, f'unexpected {node.tag!r} element'
		for key, (modifier, _) in self.children.items():
			nodes = children[key]
			if modifier == 'one':
				assert len(nodes) == 1, f'expected one of {key}, got {len(nodes)}'
				nodes = nodes[0]
			elif modifier == 'optional':
				assert len(nodes) <= 1, f'expected at most one of {key}, got {len(nodes)}'
				nodes = nodes[0] if nodes else None
			else:
				assert modifier == 'many'
			args[key] = nodes

		return self.cls(**args)

	def serialize(self, el) -> xml.Element:
		attrs = {}
		for key, (_, kind) in self.attrs.items():
			if (value := getattr(el, key)) != None:
				attrs[key] = (kind, value) + {'Float': (7,), 'Double': (19,)}.get(kind, ())
		children = []
		for key, (kind, serializers) in self.children.items():
			value = getattr(el, key)
			if kind != 'many':
				value = [value] if value != None else []
			children += ( serializers[type(el)].serialize(el) for el in value )
		return xml.Element(self.tag, attrs, children)

	__serializers = {}

	@staticmethod
	def get(element_cls: type):
		if (x := ElementSerializer.__serializers.get(element_cls)) == None:
			x = ElementSerializer.__serializers[element_cls] = ElementSerializer(element_cls)
		return x

# schema types:

ProjectTime = Double # seconds, 10 digits

@unique
class SampleFormat(IntEnum):
	INT16 = 0x20001
	INT24 = 0x40001
	FLOAT = 0x4000F

TagName = Literal[
	'COMMENTS',
	'GENRE',
	'YEAR',
	'TRACKNUMBER',
	'ALBUM',
	'TITLE',
	'ARTIST',
]

@dataclass
class Tag(BaseElement):
	name: str
	value: str

@dataclass
class Tags(BaseElement):
	tags: list[Tag]

@dataclass
class Effects(BaseElement):
	active: bool
	# TODO

@dataclass
class Label(BaseElement):
	t: ProjectTime
	t1: ProjectTime
	title: str
	selLow: ProjectTime
	selHigh: ProjectTime

@dataclass
class LabelTrack(BaseElement):
	name: str
	isSelected: bool
	height: int # px
	minimized: bool

	numlabels: int
	labels: list[Label]

@dataclass
class ControlPoint(BaseElement):
	t: ProjectTime
	val: Double

@dataclass
class Envelope(BaseElement):
	numpoints: SizeT
	points: list[ControlPoint]

@dataclass
class TimeTrack(BaseElement):
	name: str
	isSelected: bool
	height: int # px
	minimized: bool

	rangelower: Double # 0.2, 12
	rangeupper: Double # 2.0, 12
	displaylog: bool
	interpolatelog: bool

	envelope: Envelope

@dataclass
class WaveBlock(BaseElement):
	start: LongLong # sample number
	blockid: LongLong

@dataclass
class Sequence(BaseElement):
	maxsamples: SizeT
	numsamples: LongLong
	sampleformat: SizeT # SampleFormat
	effectivesampleformat: SizeT # SampleFormat
	blocks: list[WaveBlock]

@dataclass
class WaveClip(BaseElement):
	offset: ProjectTime
	trimLeft: ProjectTime
	trimRight: ProjectTime
	centShift: int
	pitchAndSpeedPreset: Long
	rawAudioTempo: Double # bpm
	clipStretchRatio: Double
	name: str
	colorindex: int

	sequence: Sequence
	envelope: Envelope

@dataclass
class WaveTrack(BaseElement):
	name: str
	isSelected: bool
	height: int # px
	minimized: bool

	colorindex: int # 0
	channel: int # 0
	linked: int # 0
	mute: bool
	solo: bool
	rate: Double # 44100.0, -1
	gain: Double # 1.0, -1
	pan: Double # 0.0, -1
	sampleformat: Long # SampleFormat
	# offset: ProjectTime ?

	clips: list[WaveClip]
	effects: Optional[Effects] = None

	# for stereo tracks:
	height1: Optional[int] = None
	minimized1: Optional[bool] = None

@dataclass
class Project(BaseElement):
	version: str # '1.3.0'
	audacityversion: str # '3.7.3'

	rate: Double # Hz, -1
	selectionformat: str # 'hh:mm:ss + milliseconds'
	frequencyformat: str # 'Hz'
	bandwidthformat: str # 'octaves'
	time_signature_tempo: Double # bpm, -1
	time_signature_upper: int # 4
	time_signature_lower: int # 4
	snapto: str # 'off'
	sel0: ProjectTime
	sel1: ProjectTime
	selLow: ProjectTime
	selHigh: ProjectTime
	vpos: int # 0
	h: Double # 0.0, 10
	zoom: ProjectTime
	preferred_export_rate: Double # Hz or 0, -1

	effects: Effects
	tags: Tags
	tracks: list[WaveTrack | LabelTrack | TimeTrack]

ElementSerializer.get(Project)
