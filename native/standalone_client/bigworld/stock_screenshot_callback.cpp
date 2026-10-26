#include "pch.hpp"
#include "stock_screenshot_callback.hpp"
#include "cstdmf/bgtask_manager.hpp"
#include "cstdmf/string_utils.hpp"
#include "moo/render_context.hpp"
#include "pyscript/script.hpp"

BW_BEGIN_NAMESPACE
int StockScreenshotCallback_token = 1;

namespace Moo { extern RenderContext *g_RC; }

namespace
{
std::weak_ptr<wot::StockScreenshotCallback> registeredScreenshotOwner;

class ScreenshotSaveTask : public BackgroundTask
{
public:
    ScreenshotSaveTask(const ComObjectWrap<DX::Surface> &surface,
        D3DXIMAGE_FILEFORMAT format, const BW::string &path,
        const std::shared_ptr<wot::StockScreenshotCallback> &owner)
        : BackgroundTask("StockScreenshotSave"), surface_(surface), format_(format),
          path_(path), owner_(owner), mutex_(Moo::rc().getD3DXCreateMutex()), saved_(false)
    {}

    virtual void doBackgroundTask(TaskManager &manager)
    {
        BW::wstring widePath;
        bw_utf8tow(path_, widePath);
        {
            // The public device is created with D3DCREATE_MULTITHREADED.
            // ScriptApp drains these tasks before Python/renderer teardown.
            SimpleMutexHolder lock(mutex_);
            saved_ = SUCCEEDED(D3DXSaveSurfaceToFileW(widePath.c_str(), format_,
                surface_.pComObject(), NULL, NULL));
        }
        surface_ = NULL;
        if (!saved_)
            ERROR_MSG("Screenshot: unable to save image %s\n", path_.c_str());
        manager.addMainThreadTask(this);
    }

    virtual void doMainThreadTask(TaskManager &)
    {
        // 924580 notifies only after a successful background save. It selects
        // the current callback at completion, not at the capture request.
        if (!owner_->complete(saved_, std::string(path_.c_str(), path_.size())))
            PyErr_Print();
        if (saved_)
            INFO_MSG("Screenshot saved - %s\n", path_.c_str());
    }

private:
    ComObjectWrap<DX::Surface> surface_;
    D3DXIMAGE_FILEFORMAT format_;
    BW::string path_;
    std::shared_ptr<wot::StockScreenshotCallback> owner_;
    SimpleMutex &mutex_;
    bool saved_;
};

class ScreenshotCaptureTask : public BackgroundTask
{
public:
    ScreenshotCaptureTask(const BW::string &format, const BW::string &name)
        : BackgroundTask("StockScreenshotCapture"), format_(format), name_(name) {}
    virtual void doBackgroundTask(TaskManager &) {}
    virtual void doMainThreadTask(TaskManager &manager)
    {
        // 655c30 discards capture work once the task manager is shutting down.
        if (manager.numUnstoppedThreads() != 0 && Moo::g_RC && Moo::g_RC->isValid())
            Moo::g_RC->screenShot(format_, name_);
    }
private:
    BW::string format_;
    BW::string name_;
};

class ScreenshotScriptShutdown : public Script::FiniTimeJob
{
    virtual void fini()
    {
        std::shared_ptr<wot::StockScreenshotCallback> owner = registeredScreenshotOwner.lock();
        if (owner)
            owner->close();
        registeredScreenshotOwner.reset();
    }
} screenshotScriptShutdown;
}

bool queueStockScreenshotCapture(const BW::string &format, const BW::string &name)
{
    if (!BgTaskManager::pInstance() || BgTaskManager::instance().numUnstoppedThreads() == 0)
    {
        ERROR_MSG("Screenshot: task manager is unavailable\n");
        return false;
    }
    BgTaskManager::instance().addMainThreadTask(new ScreenshotCaptureTask(format, name));
    return true;
}

bool queueStockScreenshotSave(const ComObjectWrap<DX::Surface> &surface,
    D3DXIMAGE_FILEFORMAT format, const BW::string &path,
    const std::shared_ptr<wot::StockScreenshotCallback> &owner)
{
    if (!BgTaskManager::pInstance() || BgTaskManager::instance().numUnstoppedThreads() == 0)
    {
        ERROR_MSG("Screenshot: task manager is unavailable\n");
        return false;
    }
    BgTaskManager::instance().addBackgroundTask(new ScreenshotSaveTask(surface, format, path, owner));
    return true;
}

static PyObject *py_wg_setScreenshotNotifyCallback(PyObject *args)
{
    PyObject *callback = NULL;
    if (!PyArg_UnpackTuple(args, "wg_setScreenshotNotifyCallback", 1, 1, &callback))
        return NULL;
    std::shared_ptr<wot::StockScreenshotCallback> owner = Moo::rc().stockScreenshotCallback();
    registeredScreenshotOwner = owner;
    // 654780 accepts any PyObjectPtr. None clears it; callability is checked
    // only when a successfully saved screenshot is delivered to Python.
    if (!owner->set(callback))
    {
        PyErr_SetString(PyExc_RuntimeError, "Screenshot notifications are shutting down");
        return NULL;
    }
    Py_RETURN_NONE;
}
PY_MODULE_FUNCTION(wg_setScreenshotNotifyCallback, BigWorld)

BW_END_NAMESPACE
