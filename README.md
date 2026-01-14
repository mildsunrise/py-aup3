# aup3

Pure Python parser for Audacity project files and related structures.

### Why

idk man I fell into a rabbit hole and it's too late now

### Dependencies

Numpy (only if you want to access the sampleblocks) and the `sqlite3` builtin Python module.

### License

MIT

## Usage

The main API is the `AUP3` class, which can read and modify the data in an `.aup3` file
(which is really just an sqlite database used as a key/value store). Once open you can
read and set the 'project XML', which contains virtually all of the project's data except
for the actual audio samples.

If you're not familiar with the data model of an Audacity project, I suggest you start
by printing the raw XML tree (a basic effort is done at a pretty-printed representation)
accessible through the `raw_project` attribute:

```python
from aup3 import AUP3
proj_db = AUP3('path/to/my/project.aup3')
print(proj_db.raw_project)
```

To actually manipulate the data you can use the dataclass-parsed representation in `project`.
For example, to list the tracks on an Audacity project:

```python
project = proj_db.project
for track in project.tracks:
    print(f' - Track {track.name!r} of type {type(track).__name__}')
```

This project comes with type hints, and it's strongly recommended to make use of them.
The data model of an Audacity project (which the parsed XML is validated against when
the `project` attribute is read) can be found in the [`project`](aup3/project.py) module.
Because I'll likely be too depressed to maintain this, you may need to first look at
the raw XML tree in `raw_project` and make some small changes to bring it up to date with
newer versions of Audacity ;( This project was written against 3.7.3 and I'm cautiously
confident the schema has full coverage for it.

The other thing an `.aup3` file contains is 'sampleblocks', which are small (up to 1MB)
arrays of audio samples with little additional metadata. These blocks are referenced
by `WaveBlock` objects inside a `WaveClip` object (which is the minimum unit of audio
that the user can interact with through the GUI). Sampleblocks can be fetched as NumPy
arrays through the `AUP3.get_block` method, but since sampleblocks carry no channel
information, for non-mono tracks you'll need to reshape the array into the right
number of channels. Each sampleblock can be stored in a different sample format (see
`project.SampleFormat`) but by default `get_block` will always return a float32 array
(other sample formats are converted to float32 and normalized to -1..+1 range). If you
don't want this, pass `normalized=False` and an array of the right dtype will be returned
depending on the stored sampleformat, without any manipulation.

If you wish to modify the project data, you can simply write the attribute and it will
be updated / committed into the database. You have to actually assign the attribute;
mutating it is not enough. Reading `project` fetches and parses a brand new object from
the database, so save it into a variable, modify it as needed, then write it back.
Same goes for `raw_project`.

Be aware some details about the encoding are not preserved in the dataclass representation
(in particular, when Audacity encodes a floating point attribute, it also encodes a "number
of digits" and this will get replaced with 7 and 19 for single and double precision floats
respectively) so you might want to make a backup just in case. For consistency reasons
it's a bad idea to have the file open in Audacity while it's being modified (see also the
`close` method). Also Audacity has these redundant `num<thing>` attributes which should
be kept in sync with the amount of things you put as children.

## Other APIs

For more advanced use cases like forensics, you might want to use the other modules directly:

 - **`xml`**: [de]serializer for Audacity's seemingly custom binary XML serialization scheme.
   Includes:

   - A low-level streaming parser to convert between the binary stream and an iterator of
     `Token` objects. These are are low-level commands: they start a tag, end a tag, append
     an attribute, append a text node or define an interned string (for later referencing by
     a tag or attribute command). This layer is fully lossless.

   - A high-level layer that converts between the `Token` stream and a tree representation of
     the XML data. It will enforce tree structure (no stray or duplicate attributes, no
     stray or invalid end tag commands) and de-intern the strings. A few properties of the
     low-level stream are lost (set/order of interned strings, choice of string encoding)
     but is still otherwise lossless as it makes no assumptions on the model of the data.

   - A convenience `decode` / `encode` API that combines these two layers into a full parser
     for the two BLOBs found in the `.aup3`, as well as `get_root` / `make_root` which handle
     the initial validation / normalization of a parsed XML document.

 - **`project`**: [un]marshaller of a parsed XML tree (from the `xml` module) into Python
   dataclasses representing each XML element. The XML data is validated against the dataclass
   annotations, which also specify which kind of attribute type is used in the XML binary
   serialization (int, long, longlong, size_t, bool, string, float, double) and this validation
   is strict by default, meaning it will raise an error upon encountering any unexpected
   attribute or element. The unmarshaller is rather basic (it doesn't support text nodes)
   but appears to be sufficient for Audacity's data model.

   This module also contains the dataclass definitions for Audacity's project data model.

 - **`aup3`**: This is the main API, described above, which can manipulate `.aup3` files.

## Wishlist

 - Full write support (methods to delete, modify or add sampleblocks are currently missing)
 - Verify everything (including attr and child node order) against the C++ source code
 - Add enum support to the unmarshaller so that we can make e.g. `sampleformat` fields
   have the right enum type rather than `int` or `SizeT`
 - Validate/remove the `num<thing>` attributes when parsing, add them back when serializing
 - Unit tests
 - This can theoretically made to work all the way up to python 3.9 (when `Annotated` was
   introduced); ensure it's the case
 - Ideally make `ElementSerializer` a metaclass, like in [py-struct](https://github.com/mildsunrise/py-struct)
 - API to create projects from scratch
 - Ability to open in read-only mode
