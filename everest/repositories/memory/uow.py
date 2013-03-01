"""
Unit of work.

This file is part of the everest project. 
See LICENSE.txt for licensing, CONTRIBUTORS.txt for contributor information.

Created on Jan 16, 2013.
"""
from collections import defaultdict
from weakref import WeakSet
from weakref import ref

__docformat__ = 'reStructuredText en'
__all__ = ['OBJECT_STATES',
           'UnitOfWork',
           ]


class OBJECT_STATES(object):
    CLEAN = 'CLEAN'
    NEW = 'NEW'
    DELETED = 'DELETED'
    DIRTY = 'DIRTY'


class EntityStateManager(object):
    """
    Helper object to track object state.
    
    Initially, an object is marked as NEW (freshly instantiated) or CLEAN
    (freshly fetched from repository).
    
    Only a weak reference to the tracked object is stored to avoid circular
    references.
    
    Allowed transitions are CLEAN -> DIRTY, CLEAN -> DELETED, NEW -> DIRTY, 
    NEW -> DELETED, DIRTY -> DELETED, DIRTY -> CLEAN.
    """
    __allowed_transitions = ((None, OBJECT_STATES.NEW),
                             (None, OBJECT_STATES.CLEAN),
                             (OBJECT_STATES.NEW, OBJECT_STATES.CLEAN),
                             (OBJECT_STATES.NEW, OBJECT_STATES.DELETED),
                             (OBJECT_STATES.CLEAN, OBJECT_STATES.DIRTY),
                             (OBJECT_STATES.CLEAN, OBJECT_STATES.DELETED),
                             (OBJECT_STATES.DIRTY, OBJECT_STATES.CLEAN),
                             (OBJECT_STATES.DIRTY, OBJECT_STATES.DELETED),
                             )

    def __init__(self, entity):
        self.__obj_ref = ref(entity)
        self.__state = None
        self.__last_state_hash = hash(self.__get_state_string())

    def reset_state(self):
        self.state = OBJECT_STATES.CLEAN
        self.__last_state_hash = hash(self.__get_state_string())

    @classmethod
    def clone(cls, entity):
        clone = object.__new__(entity.__class__)
        data = EntityStateManager.get_state_data(entity)
        EntityStateManager.set_state_data(clone, data)
        return clone

    @classmethod
    def track(cls, entity, state):
        entity.__everest__ = EntityStateManager(entity)
        entity.__everest__.state = state

    @staticmethod
    def get_state_data(entity):
        return dict([(attr_name, attr_value)
                     for attr_name, attr_value in entity.__dict__.iteritems()
                     if not attr_name.startswith('_')])

    @staticmethod
    def set_state_data(entity, data):
        for attr_name, attr_value in data.iteritems():
            setattr(entity, attr_name, attr_value)

    def __get_state_string(self):
        # Concatenate all public attribute name:value pairs.
        data = EntityStateManager.get_state_data(self)
        tokens = ['%s:%s' % (k, v)
                  for (k, v) in data.iteritems()]
        return ','.join(tokens)

    def __get_state(self):
        state = self.__state
        if state == OBJECT_STATES.CLEAN:
            obj_hash = hash(self.__get_state_string())
            if obj_hash != self.__last_state_hash:
                state = OBJECT_STATES.DIRTY
        return state

    def __set_state(self, state):
        if not (self.__get_state(), state) in self.__allowed_transitions:
            raise ValueError('Invalid state transition %s -> %s.'
                             % (self.__state, state))
        self.__state = state

    state = property(__get_state, __set_state)


class UnitOfWork(object):
    """
    The Unit Of Work records object state changes for subsequent commit
    to a repository.
    
    Responsibilities:
     * Clone CLEAN entities upon registration;
     * Record entity state changes.
    """
    def __init__(self):
        self.__entity_set_map = defaultdict(WeakSet)

    def register_new(self, entity_class, entity):
        """
        Registers the given entity for the given class as NEW.
        
        :raises ValueError: If the given entity already holds state.
        """
        if hasattr(entity, '__everest__'):
            raise ValueError('Trying to register a NEW entity that has '
                             'already been registered!')
        EntityStateManager.track(entity, OBJECT_STATES.NEW)
        self.__entity_set_map[entity_class].add(entity)

    def register_clean(self, entity_class, entity):
        """
        Registers the given entity for the given class as CLEAN.
        
        :returns: Cloned entity.
        """
        clone = EntityStateManager.clone(entity)
        EntityStateManager.track(clone, OBJECT_STATES.CLEAN)
        self.__entity_set_map[entity_class].add(entity)
        return clone

    def unregister(self, entity_class, entity):
        """
        Unregisters the given entity for the given class and discards its
        state information.
        """
        if not hasattr(entity, '__everest__'):
            raise ValueError('Trying to unregister an entity that has not '
                             'been registered yet!')
        self.__entity_set_map[entity_class].remove(entity)
        delattr(entity, '__everest__')

    def mark_clean(self, entity_class, entity):
        """
        Marks the given entity for the given class as CLEAN.
        
        This is done when an entity is loaded fresh from the repository or
        after a commit.
        """
        if not hasattr(entity, '__everest__'):
            raise ValueError('Trying to mark an unregistered entity as '
                             'CLEAN!')
        entity.__everest__.reset_state()
        self.__entity_set_map[entity_class].add(entity)

    def mark_deleted(self, entity_class, entity):
        """
        Marks the given entity for the given class as DELETED.
        
        :raises ValueError: If the given entity does not hold state.
        """
        if not hasattr(entity, '__everest__'):
            raise ValueError('Trying to mark an unregistered entity as '
                             'DELETED!')
        entity.__everest__.state = OBJECT_STATES.DELETED
        self.__entity_set_map[entity_class].add(entity)

    def mark_dirty(self, entity_class, entity):
        """
        Marks the given entity for the given class as DIRTY.
        
        :raises ValueError: If the given entity does not hold state.
        """
        if not hasattr(entity, '__everest__'):
            raise ValueError('Trying to mark an unregistered entity as '
                             'DIRTY!')
        entity.__everest__.state = OBJECT_STATES.DIRTY
        self.__entity_set_map[entity_class].add(entity)

    def get_clean(self, entity_class=None):
        """
        Returns an iterator over all CLEAN entities in this unit of work
        (optionally restricted to entities of the given class).
        """
        return self.__object_iterator(OBJECT_STATES.CLEAN, entity_class)

    def get_new(self, entity_class=None):
        """
        Returns an iterator over all NEW entities in this unit of work
        (optionally restricted to entities of the given class).
        """
        return self.__object_iterator(OBJECT_STATES.NEW, entity_class)

    def get_deleted(self, entity_class=None):
        """
        Returns an iterator over all DELETED entities in this unit of work
        (optionally restricted to entities of the given class).
        """
        return self.__object_iterator(OBJECT_STATES.DELETED, entity_class)

    def get_dirty(self, entity_class=None):
        """
        Returns an iterator over all DIRTY entities in this unit of work
        (optionally restricted to entities of the given class).
        """
        return self.__object_iterator(OBJECT_STATES.DIRTY, entity_class)

    def iterator(self):
        for ent_cls in self.__entity_set_map.keys():
            for ent in self.__entity_set_map[ent_cls]:
                yield ent_cls, ent, ent.__everest__.state

    def reset(self):
        self.__entity_set_map.clear()

    def __object_iterator(self, state, ent_cls):
        if ent_cls is None:
            keys = self.__entity_set_map.keys()
        else:
            keys = [ent_cls]
        for key in keys:
            for ent in self.__entity_set_map[key]:
                if ent.__everest__.state == state:
                    yield ent