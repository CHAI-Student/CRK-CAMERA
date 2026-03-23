#include <gst/gst.h>
#include <iostream>

int main(int argc, char *argv[]) {
    // 1. Initialize GStreamer
    gst_init(&argc, &argv);

    // 2. Create Elements
    GstElement *pipeline  = gst_pipeline_new("gst-nvjpegdec-pipeline");
    GstElement *source    = gst_element_factory_make("fdsrc", "source");
    GstElement *decoder   = gst_element_factory_make("nvjpegdec", "decoder");
    GstElement *filter    = gst_element_factory_make("capsfilter", "filter");
    GstElement *sink      = gst_element_factory_make("fdsink", "sink");

    if (!pipeline || !source || !decoder || !filter || !sink) {
        std::cerr << "One or more elements could not be created." << std::endl;
        return -1;
    }

    // 3. Configure Source Element Properties
    // Equivalent to fd=0 (stdin)
    g_object_set(G_OBJECT(source), "fd", 0, NULL);

    // 4. Configure Caps Filter
    // Equivalent to 'video/x-raw, format=I420'
    GstCaps *caps = gst_caps_from_string("video/x-raw, format=I420");
    g_object_set(G_OBJECT(filter), "caps", caps, NULL);
    gst_caps_unref(caps);

    // 5. Configure Sink Element Properties
    // Equivalent to fd=1 (stdout)
    g_object_set(G_OBJECT(sink), "fd", 1, NULL);

    // 6. Build the Pipeline
    gst_bin_add_many(GST_BIN(pipeline), source, decoder, filter, sink, NULL);
    if (!gst_element_link_many(source, decoder, filter, sink, NULL)) {
        std::cerr << "Elements could not be linked." << std::endl;
        gst_object_unref(pipeline);
        return -1;
    }

    // 7. Set Pipeline State to Playing
    GstStateChangeReturn ret = gst_element_set_state(pipeline, GST_STATE_PLAYING);
    if (ret == GST_STATE_CHANGE_FAILURE) {
        std::cerr << "Unable to set the pipeline to the playing state." << std::endl;
        gst_object_unref(pipeline);
        return -1;
    }

    // 8. Wait for EOS or Error on the Bus
    GstBus *bus = gst_element_get_bus(pipeline);
    GstMessage *msg = gst_bus_timed_pop_filtered(bus, GST_CLOCK_TIME_NONE,
                        (GstMessageType)(GST_MESSAGE_ERROR | GST_MESSAGE_EOS));

    // 9. Parse Message and Cleanup
    if (msg != NULL) {
        GError *err;
        gchar *debug_info;

        switch (GST_MESSAGE_TYPE(msg)) {
            case GST_MESSAGE_ERROR:
                gst_message_parse_error(msg, &err, &debug_info);
                std::cerr << "Error received from element " << GST_OBJECT_NAME(msg->src) 
                          << ": " << err->message << std::endl;
                g_clear_error(&err);
                g_free(debug_info);
                break;
            case GST_MESSAGE_EOS:
                std::cerr << "End-Of-Stream reached." << std::endl;
                break;
            default:
                break;
        }
        gst_message_unref(msg);
    }

    gst_object_unref(bus);
    gst_element_set_state(pipeline, GST_STATE_NULL);
    gst_object_unref(pipeline);

    return 0;
}