''' Simplified .aup3 file interface '''

from .xml import encode, decode, get_root, make_root, Element
from .project import ElementSerializer, Project, SampleFormat
import sqlite3

__all__ = ['AUP3']

EXPECTED_DOCTYPE = '''
<?xml version="1.0" standalone="no" ?>
<!DOCTYPE project PUBLIC "-//audacityproject-1.3.0//DTD//EN" "http://audacity.sourceforge.net/xml/audacityproject-1.3.0.dtd" >
'''.strip()

FORMAT_DATA = {
	SampleFormat.INT16: ('int16', 0x8000),
	SampleFormat.INT24: ('int32', 0x800000),
	SampleFormat.FLOAT: ('float32', 1),
}

class AUP3:
	def __init__(self, path: str, ignore_autosave=False):
		'''
		opens an .aup3 file and (by default) performs some initial health checks.
		if you're going to modify the file (e.g. by writing to `[raw_]project`)
		it's highly recommended that the file isn't open in audacity at the same time.
		'''
		self.conn = sqlite3.Connection(path)
		assert self.conn.execute('SELECT count(*) FROM project').fetchone()[0] == 1
		if not ignore_autosave:
			assert (n := self.conn.execute('SELECT count(*) FROM autosave').fetchone()[0]) == 0, \
				f'{n} autosave records found'

	@property
	def raw_project(self) -> Element:
		'''
		project structure, as parsed XML tree. unlike `project`, this will always work
		unless the XML binary serialization format has changed, and re-serialization is
		almost byte-for-byte identical (save for [the order in] which strings are interned).

		note: for forensic use cases where absolute byte preservation is required,
		you might want to use the `xml` module's `TokenSerializer` class directly.
		'''
		names, doc = self.conn.execute('SELECT dict, doc FROM project').fetchone()
		doctype, root = get_root(decode(names, doc))
		assert doctype.strip() == EXPECTED_DOCTYPE,	f'unexpected doctype: {doctype!r}'
		return root

	@raw_project.setter
	def raw_project(self, value: Element):
		names, doc = encode(make_root(value, EXPECTED_DOCTYPE))
		res = self.conn.execute('UPDATE project SET dict = ?, doc = ?', [names, doc])
		assert res.rowcount == 1
		self.conn.commit()

	@property
	def project(self) -> Project:
		'''
		like `raw_project` but the XML data is validated and unmarshalled into Python
		dataclass objects. because Audacity doesn't have a well documented schema (its .dtd
		is hilariously out of date) this module makes no effort at backcompat and will
		raise an exception upon encountering *any* unknown attribute; in that case you
		may need to modify the dataclass types in `audacity_project` or use `raw_project`.

		this API is also a bit more lossy than `raw_project`: re-serialization does not
		preserve the order of attributes as well as child nodes belonging to different attributes,
		and the amount of significant digits is replaced with 7 (floats) and 19 (doubles)
		like in audacity-project-tools. also, the serializer is more lenient than the
		de-serializer as it assumes you're statically checking your Python code.
		'''
		return ElementSerializer.get(Project).deserialize(self.raw_project) # type: ignore

	@project.setter
	def project(self, value: Project):
		self.raw_project = ElementSerializer.get(Project).serialize(value)

	def get_block(self, id: int, normalized=True):
		'''
		returns sampleblock as a numpy array, optionally normalized to -1..+1 floats.
		WARNING: array must be manually reshaped according to channels
		'''
		import numpy
		format, samples = self.conn.execute('SELECT sampleformat, samples FROM sampleblocks WHERE blockid = ?', [id]).fetchone()
		dtype, factor = FORMAT_DATA[format]
		samples = numpy.frombuffer(samples, dtype=dtype)
		if normalized:
			samples = samples.astype('float32')
			samples /= factor
		return samples

	# TODO: add_block, set_block (maybe keep sampleformat by default)

	def close(self):
		''' explicitly close the .aup3 file (done automatically when self is deleted) '''
		self.conn.close()
