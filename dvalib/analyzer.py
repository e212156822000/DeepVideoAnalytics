from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
import math
import sys
import os.path
from PIL import Image
import logging
import numpy as np

try:
    if os.environ.get('PYTORCH_MODE',True):
        import dvalib.crnn.utils as utils
        import dvalib.crnn.dataset as dataset
        import torch
        from torch.autograd import Variable
        import dvalib.crnn.models.crnn as crnn
        logging.info("In pytorch mode, not importing TF")
    else:
        logging.warning("Not importing anything assuming Caffe mode")
except ImportError:
    import tensorflow as tf
    from tensorflow.contrib.slim.python.slim.nets import inception
    from tensorflow.python.training import saver as tf_saver
    slim = tf.contrib.slim


class BaseAnnotator(object):

    def __init__(self):
        pass

    def apply(self,image_path):
        pass


def inception_preprocess(image, central_fraction=0.875):
    image = tf.cast(tf.image.decode_jpeg(image, channels=3), tf.float32)
    # image = tf.image.central_crop(image, central_fraction=central_fraction)
    image = tf.expand_dims(image, [0])
    # TODO try tf.image.resize_image_with_crop_or_pad and tf.image.extract_glimpse
    image = tf.image.resize_bilinear(image, [299, 299], align_corners=False)
    # Center the image about 128.0 (which is done during training) and normalize.
    image = tf.multiply(image, 1.0 / 127.5)
    return tf.subtract(image, 1.0)

class OpenImagesAnnotator(BaseAnnotator):

    def __init__(self):
        super(OpenImagesAnnotator, self).__init__()
        self.name = "inception"
        self.net = None
        self.tf = True
        self.session = None
        self.graph_def = None
        self.input_image = None
        self.predictions = None
        self.num_classes = 6012
        self.top_n = 25
        self.labelmap_path = os.path.abspath(__file__).split('annotator.py')[0]+'data/labelmap.txt'
        self.dict_path = os.path.abspath(__file__).split('annotator.py')[0]+'data/dict.csv'
        self.labelmap = [line.rstrip() for line in file(self.labelmap_path).readlines()]
        if len(self.labelmap) != self.num_classes:
            logging.error("{} lines while the number of classes is {}".format(len(self.labelmap),self.num_classes))
        self.label_dict = {}
        for line in tf.gfile.GFile(self.dict_path).readlines():
            words = [word.strip(' "\n') for word in line.split(',', 1)]
            self.label_dict[words[0]] = words[1]

    def load(self):
        if self.session is None:
            logging.warning("Loading the network {} , first apply / query will be slower".format(self.name))
            config = tf.ConfigProto()
            config.gpu_options.per_process_gpu_memory_fraction = 0.15
            network_path = os.path.abspath(__file__).split('annotator.py')[0]+'data/2016_08/model.ckpt'
            g = tf.Graph()
            with g.as_default():
                self.input_image = tf.placeholder(tf.string)
                processed_image = inception_preprocess(self.input_image)
                with slim.arg_scope(inception.inception_v3_arg_scope()):
                    logits, end_points = inception.inception_v3(processed_image, num_classes=self.num_classes, is_training=False)
                self.predictions = end_points['multi_predictions'] = tf.nn.sigmoid(logits, name='multi_predictions')
                saver = tf_saver.Saver()
                self.session = tf.InteractiveSession(config=config)
                saver.restore(self.session, network_path)

    def apply(self,image_path):
        if self.session is None:
            self.load()
        img_data = tf.gfile.FastGFile(image_path).read()
        predictions_eval = np.squeeze(self.session.run(self.predictions, {self.input_image: img_data}))
        results = {self.label_dict.get(self.labelmap[idx], 'unknown'):predictions_eval[idx]
                   for idx in predictions_eval.argsort()[-self.top_n:][::-1]}
        return results


class CRNNAnnotator(BaseAnnotator):

    def __init__(self):
        super(CRNNAnnotator, self).__init__()
        self.session = None
        self.model_path = '/root/DVA/dvalib/crnn/data/crnn.pth'
        self.alphabet = '0123456789abcdefghijklmnopqrstuvwxyz'
        self.cuda = False

    def load(self):
        if torch.cuda.is_available():
            self.session = crnn.CRNN(32, 1, 37, 256, 1).cuda()
            self.cuda = True
        else:
            self.session = crnn.CRNN(32, 1, 37, 256, 1)
        self.session.load_state_dict(torch.load(self.model_path))
        self.converter = utils.strLabelConverter(self.alphabet)
        self.transformer = dataset.resizeNormalize((100, 32))

    def apply(self,image_path):
        image = Image.open(image_path).convert('L')
        if self.cuda:
            image = self.transformer(image).cuda()
        else:
            image = self.transformer(image)
        image = image.view(1, *image.size())
        image = Variable(image)
        self.session.eval()
        preds = self.session(image)
        _, preds = preds.max(2)
        preds = preds.squeeze(2)
        preds = preds.transpose(1, 0).contiguous().view(-1)
        preds_size = Variable(torch.IntTensor([preds.size(0)]))
        sim_pred = self.converter.decode(preds.data, preds_size.data, raw=False)
        return sim_pred


