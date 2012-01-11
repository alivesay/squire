#include <Python.h>
#include <stdio.h>
#include <sys/types.h>
#include <sys/inotify.h>
#include <error.h>

/*
 *
 * squiredmodule.c
 *
 * Provides lightweight inotify API interface for squired.py
 *
 */

#define MAX_EVENTS         1024
#define INOTIFY_EVENT_SIZE (sizeof(struct inotify_event))
#define INOTIFY_BUF_LEN    (MAX_EVENTS * (INOTIFY_EVENT_SIZE + 16))

int inotify_fd = -1;

/* inotify_init */
static PyObject *
squired_inotify_init(PyObject *self, PyObject *args)
{
    inotify_fd = inotify_init();

    return Py_None;
}

/* inotify add_watch */
static PyObject *
squired_add_watch(PyObject *self, PyObject *args)
{
    const char *path = NULL;

    int opts = 0;

    if (!PyArg_ParseTuple(args, "si", &path, &opts))
        return Py_None;

    return Py_BuildValue("i", inotify_add_watch(inotify_fd, path, opts));
}

/* inotify rm_watch */
static PyObject *
squired_rm_watch(PyObject *self, PyObject *args)
{
    int wd = 0;

    if (!PyArg_ParseTuple(args, "i", &wd))
        return Py_None;
    
    return Py_BuildValue("i", inotify_rm_watch(inotify_fd, wd));
}

/* inotify select */
static PyObject *
squired_select(PyObject *self, PyObject *args)
{
     struct timeval time = {0};

     int num_events = 0;
     int count      = 0;
     int len        = 0;
     int rtv        = 0;
     int i          = 0;

     char buf[INOTIFY_BUF_LEN];

     struct inotify_event* event = (struct inotify_event *)NULL;

     PyObject *events    = (PyObject *)NULL;
     PyObject *new_event = (PyObject *)NULL;

     fd_set rfds;

     /* set timeout */
     time.tv_sec = 1;

     /* clear set */
     FD_ZERO(&rfds);
 
     /* add inotify_fd to to set */
     FD_SET(inotify_fd, &rfds);

     /* poll for events */
     rtv = select(inotify_fd + 1, &rfds, NULL, NULL, &time);

     if (rtv < 0) {
         if (errno == EINTR) {
             /* busy */
             return Py_BuildValue("{s:s}", "OKAY", "BUSY");
         } else {
             /* error */
             return Py_BuildValue("{s:s}", "ERROR", "FD SELECT ERROR");
         }

     } else if (0 == rtv) {
         /* timeout */
         return Py_BuildValue("{s:s}", "OKAY", "TIMEOUT");

     } else if (FD_ISSET(inotify_fd, &rfds)) {
        /* events available */

        /* read events into buffer */
        len = read(inotify_fd, buf, INOTIFY_BUF_LEN);

        /* read() failed? */
        if (len == -1) {
            return Py_BuildValue("{s:s}", "ERROR", "read() ERROR");
        }

        /* less than a complete inotify_event in buffer? */
        if (len < INOTIFY_EVENT_SIZE) {
            return Py_BuildValue("{s:s}", "ERROR", "len < INOTIFY_EVENT_SIZE");
        }
        
        /* count number of events */
        num_events = 0;
        while (i < len) {
            num_events++;
            event = (struct inotify_event*) &buf[i];
            i += INOTIFY_EVENT_SIZE + event->len;
        }

        if (num_events > MAX_EVENTS) {
            return Py_BuildValue("{s:s}", "ERROR", "num_events > MAX_EVENTS");
        }

        /* allocate PyList for events */
        events = PyList_New(num_events);
        if (!events) {
            return Py_BuildValue("{s:s}", "ERROR", "PyList_New() FAILED");
        }
        
        /* load events into PyList */
        i = count = 0;

        while (i < len) {
            event = (struct inotify_event *) &buf[i];
            
            /* create PyObject dict from inotify_event  */
            new_event = Py_BuildValue("{s:i,s:i,s:i,s:i,s:s}", "watch_descriptor", event->wd,
                                                               "mask",             event->mask,
                                                               "cookie",           event->cookie,
                                                               "len",              event->len,
                                                               "name",             event->name);

            /* Py_BuildValue failed? */
            if (!new_event) {
                Py_DECREF(events);
                return Py_BuildValue("{s:s}", "ERROR", "Py_BuildValue() FAILED");
            }

            /* add event to PyList */
            PyList_SET_ITEM(events, count, new_event);

            i += INOTIFY_EVENT_SIZE + event->len;
            count++;
        }

        if (count > 0) return events;
     }

     return Py_None;
}

/* provide access to errno */
static PyObject *
squired_errno(PyObject *self, PyObject *args)
{
    return Py_BuildValue("i", errno);
}

/* cleanup */
static PyObject *
squired_shutdown(PyObject *self, PyObject *args)
{
    if (inotify_fd != -1) {
        close(inotify_fd);
    }

    return Py_None;
}

/* python-exposed methods */
static PyMethodDef SquiredMethods[] = {
    { "add_watch",    squired_add_watch,    METH_VARARGS,   "inotify_add_watch" },
    { "rm_watch",     squired_rm_watch,     METH_VARARGS,   "inotify_rm_watch"  },
    { "select",       squired_select,       METH_VARARGS,   "shutdown inotify"  },
    { "inotify_init", squired_inotify_init, 0,              "inotify_init"      },
    { "shutdown",     squired_shutdown,     0,              "shutdown inotify"  },
    { "errno",        squired_errno,        0,              "returns errno"     },
    {NULL,            NULL,                 0,              NULL                }
};

/* initialization */
void initsquired(void) {
    (void) Py_InitModule("squired", SquiredMethods);
}
