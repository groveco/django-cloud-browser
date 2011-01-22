"""Rackspace cloud wrapper."""
from datetime import datetime

try:
    import cloudfiles  # pylint: disable=F0401
except ImportError:
    cloudfiles = None  # pylint: disable=C0103

from cloud_browser.cloud import errors, base
from cloud_browser.common import SEP


# Current Rackspace maximum.
RS_MAX_GET_OBJS_LIMIT = 10000


class RackspaceExceptionWrapper(errors.CloudExceptionWrapper):
    """Exception translator."""
    translations = {
        cloudfiles.errors.NoSuchContainer: errors.NoContainerException,
        cloudfiles.errors.NoSuchObject: errors.NoObjectException,
    }
wrap_rs_errors = RackspaceExceptionWrapper()  # pylint: disable=C0103


class RackspaceObject(base.CloudObject):
    """Cloud object wrapper."""

    @wrap_rs_errors
    def _get_object(self):
        """Return native storage object."""
        return self.container.native_container.get_object(self.name)

    @wrap_rs_errors
    def _read(self):
        """Return contents of object."""
        return self.native_obj.read()

    @classmethod
    def from_info(cls, container, info_obj):
        """Create from subdirectory or file info object."""
        create_fn = cls.from_subdir if 'subdir' in info_obj \
            else cls.from_file_info
        return create_fn(container, info_obj)

    @classmethod
    def from_subdir(cls, container, info_obj):
        """Create from subdirectory info object."""
        return cls(container,
                   info_obj['subdir'],
                   obj_type=cls.type_cls.SUBDIR)

    @classmethod
    def choose_type(cls, content_type):
        """Choose object type from content type."""
        return cls.type_cls.SUBDIR if content_type == "application/directory" \
            else cls.type_cls.FILE

    @classmethod
    def from_file_info(cls, container, info_obj):
        """Create from regular info object."""
        # 2010-04-15T01:52:13.919070
        dt_str = info_obj['last_modified'].partition('.')[0]
        last_modified = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")
        return cls(container,
                   name=info_obj['name'],
                   size=info_obj['bytes'],
                   content_type=info_obj['content_type'],
                   last_modified=last_modified,
                   obj_type=cls.choose_type(info_obj['content_type']))

    @classmethod
    def from_obj(cls, container, file_obj):
        """Create from regular info object."""
        # Thu, 07 Jun 2007 18:57:07 GMT
        dt_str = file_obj.last_modified
        last_modified = datetime.strptime(dt_str, "%a, %d %b %Y %H:%M:%S GMT")
        return cls(container,
                   name=file_obj.name,
                   size=file_obj.size,
                   content_type=file_obj.content_type,
                   last_modified=last_modified,
                   obj_type=cls.choose_type(file_obj.content_type))


class RackspaceContainer(base.CloudContainer):
    """Rackspace container wrapper."""
    obj_cls = RackspaceObject

    @wrap_rs_errors
    def _get_container(self):
        """Return native container object."""
        return self.conn.native_conn.get_container(self.name)

    @wrap_rs_errors
    def get_objects(self, path, marker=None,
                    limit=base.DEFAULT_GET_OBJS_LIMIT):
        """Get objects.

        **Pseudo-directory Notes**: Rackspace has two approaches to pseudo-
        directories within the (really) flat storage object namespace:

          1. Dummy directory storage objects. These are real storage objects
             of type "application/directory" and must be manually uploaded
             by the client.
          2. Implied subdirectories using the `path` API query parameter.

        Both serve the same purpose, but the latter is much preferred because
        there is no independent maintenance of extra dummy objects, and the
        `path` approach is always correct (for the existing storage objects).

        This package uses the latter `path` approach, but gets into an
        ambiguous situation where there is both a dummy directory storage
        object and an implied subdirectory. To remedy this situation, we only
        show information for the dummy directory object in results if present,
        and ignore the implied subdirectory. But, under the hood this means
        that our `limit` parameter may end up with less than the desired
        number of objects. So, we use the heuristic that if we **do** have
        "application/directory" objects, we end up doing an extra query of
        double the limit size to ensure we can get up to the limit amount
        of objects. This double query approach is inefficient, but as
        using dummy objects should now be deprecated, the second query should
        only rarely occur.

        """
        # TODO: BUG: subdir has '/' that is stripped off, but needed when
        # passing in the marker string to list_objects_info

        # Enforce maximum object size.
        orig_limit = limit
        if limit > RS_MAX_GET_OBJS_LIMIT:
            raise errors.CloudException("Object limit must be less than %s" %
                                        RS_MAX_GET_OBJS_LIMIT)

        # Adjust limit to +1 to handle marker object as first result.
        # We can get in to this situation for a marker of "foo", that will
        # still return a 'subdir' object of "foo/" because of the extra
        # slash.
        limit += 1

        path = path + SEP if path else ''
        object_infos = self.native_container.list_objects_info(
            limit=limit, delimiter=SEP, prefix=path, marker=marker)

        def _collapse(infos):
            """Remove duplicate dummy / implied objects."""
            name = None
            for info in infos:
                name = info.get('name', name)
                subdir = info.get('subdir', '').strip(SEP)
                if not name or subdir != name:
                    yield info

        if object_infos:
            # Check first object for marker match and truncate if so.
            if marker and \
                object_infos[0].get('subdir', '').strip(SEP) == marker:
                object_infos = object_infos[1:]

            # Collapse subdirs and dummy objects.
            object_infos = list(_collapse(object_infos))

            # If we have over the original limit, truncate.
            object_infos = object_infos[:orig_limit]

        return [self.obj_cls.from_info(self, x) for x in object_infos]

    @wrap_rs_errors
    def get_object(self, path):
        """Get single object."""
        obj = self.native_container.get_object(path)
        return self.obj_cls.from_obj(self, obj)


class RackspaceConnection(base.CloudConnection):
    """Rackspace connection wrapper."""
    cont_cls = RackspaceContainer

    def __init__(self, account, secret_key, rs_servicenet=False):
        """Initializer."""
        super(RackspaceConnection, self).__init__(account, secret_key)
        self.rs_servicenet = rs_servicenet

    @wrap_rs_errors
    def _get_connection(self):
        """Return native connection object."""
        kwargs = {
            'username': self.account,
            'api_key': self.secret_key,
        }

        # Only add kwarg for servicenet if True because user could set
        # environment variable 'RACKSPACE_SERVICENET' separately.
        if self.rs_servicenet:
            kwargs['servicenet'] = True

        return cloudfiles.get_connection(**kwargs)  # pylint: disable=W0142

    @wrap_rs_errors
    def _get_containers(self):
        """Return available containers."""
        infos = self.native_conn.list_containers_info()
        return [self.cont_cls(self, i['name'], i['count'], i['bytes'])
                for i in infos]

    @wrap_rs_errors
    def _get_container(self, path):
        """Return single container."""
        cont = self.native_conn.get_container(path)
        return self.cont_cls(self,
                             cont.name,
                             cont.object_count,
                             cont.size_used)
