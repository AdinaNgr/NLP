from __future__ import print_function

from keras.models import Sequential, Model
from keras.layers.embeddings import Embedding
from keras.layers import Input, Activation, Dense, Permute, Reshape, Dropout
from keras.layers import add, dot, multiply, concatenate
from keras.layers import LSTM, Conv1D, TimeDistributed, MaxPooling1D
from keras.initializers import Constant
from keras import backend as K
import numpy as np

from dataproc_utils import *
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

np.set_printoptions(edgeitems=10)

batch_size = 64
epochs = 25
random_state = 42
n_pars = 9 # max number of paragraphs from each document
body_size = 15  # max paragraph length (num of words in each paragraph)
claim_size = 15  # max num of words in each claim
embedding_dim = 25
output_size = 4  # size of the output vector

# open saved wordvecs from file and make id dicts
w2v = load_wordvecs('twitter_glo_vecs\\wordvecs25d.txt')
print(len(w2v), 'pretrained embeddings')

# load data and labels
data = load_proc_data('processed_data\\train_bodies.txt', 'processed_data\\train_claims.txt', split_pars=True)
labels = [label for body, claim, label in data]
data = [(body, claim) for body, claim, label in data]
y = np.array(labels)


# initialize tfidf tokenizer
# fit on concatenated list of bodies and claims
# transform bodies/pars and claims separately
# measure cosine similarity btw tfidf representations of bodies & claims to compute p_tfidf (claim-evidence sim vector)
# tfidf_body shape (len(claims), n_pars, vocab size)
# tfidf_claim shape (len(claims), vocab size)
# p_tfidf output shape: (len(claims), n_pars)

# load pre-computed p_tfidf similarity matrix for train data
p_tfidf = np.loadtxt('processed_data\\p_tfidf_train.txt', dtype=np.float32)
p_tfidf = np.reshape(p_tfidf, (-1, n_pars, 1))
print('Shape of similarity matrix train p_tfidf:', p_tfidf.shape)

# train/validation split
train_data, val_data, train_p_tfidf, val_p_tfidf, train_labels, val_labels = train_test_split(data, p_tfidf, y,
                                                                                              test_size=.2,
                                                                                              random_state=random_state)
print('First input tuple (body, claim, stance):\n', train_data[0])
print('First input p_tfidf:\n', train_p_tfidf[0])


# create a vocabulary dict from train data
word2freq = make_word_freq_V(train_data, fmin=2)
word2index = word2idx(word2freq, pretrained=w2v)

vocab_size = len(word2index)
print('Vocab size:', vocab_size, 'unique words in the train set')

# vectorize input words (turn each word into its index from the word2index dict)
# for new words in test set that don't appear in train set, use index of <unknown>
train_body, train_claim = vocab_vectorizer(train_data, word2index, max_par_len=body_size, max_claim_len=claim_size)
val_body, val_claim = vocab_vectorizer(val_data, word2index, max_par_len=body_size, max_claim_len=claim_size)



# prepare embedding matrix
embedding_matrix = np.zeros((vocab_size + 1, embedding_dim))
for w, i in word2index.items():
    embedding_matrix[i] = w2v[w]

# load pre-trained word vectors into embedding layers
# we set trainable to false to keep the embeddings fixed
embedding_body = Embedding(vocab_size + 1,
                            embedding_dim,
                            embeddings_initializer=Constant(embedding_matrix),
                            input_length=(n_pars, body_size,),
                            trainable=False)

embedding_claim = Embedding(vocab_size + 1,
                            embedding_dim,
                            embeddings_initializer=Constant(embedding_matrix),
                            input_length=claim_size,
                            trainable=False)

# initialize placeholders and embed pre-trained word vectors
input_body = Input(shape=(n_pars, body_size,), dtype='int32')  # change input shape
input_claim = Input(shape=(claim_size,), dtype='int32')
const_p_tfidf = K.constant(train_p_tfidf, dtype='float32')
input_p_tfidf = Input(tensor=const_p_tfidf)

print(input_body.shape)     # (?, 9, 15)
print(input_claim.shape)    # (?, 15)
print(input_p_tfidf.shape)  # (39977, 9, 1)

embedded_body = embedding_body(input_body)
embedded_claim = embedding_claim(input_claim)

print(embedded_body.shape)   # (?, 9, 15, 25)
print(embedded_claim.shape)  # (?, 15, 25)

# train two 1D convnets with maxpooling (should be time distributed with maxout layer (??))
cnn_body = TimeDistributed(Conv1D(100, 5, padding='same', activation='relu'))(embedded_body)
cnn_body = TimeDistributed(MaxPooling1D(5, padding='same'))(cnn_body)  ## should be maxout

cnn_claim = Conv1D(100, 5, padding='same', activation='relu')(embedded_claim)
cnn_claim = MaxPooling1D(5, padding='same')(cnn_claim)

print(cnn_body.shape)  # (?, 9, 3, 100)
print(cnn_claim.shape)  # (?, 3, 100)

cnn_body = Reshape((n_pars, 300))(cnn_body)

# train two lstms
lstm_body = TimeDistributed(LSTM(100))(embedded_body)
lstm_claim = (LSTM(100))(embedded_claim)

print(lstm_body.shape) # (?, 9, 100)
print(lstm_claim.shape) # (?, 100)

lstm_body = multiply([lstm_body, input_p_tfidf])  # (multiply??)
### tensor shapes: (len(claims/bodies), n_pars (9), 100) * (len(claims/bodies), n_pars, 1)
print('lstm_body', lstm_body.shape)  # (39977, 9, 100)
print('lstm_claim', lstm_claim.shape)

## p_lstm = lstm_claim.T x M x lstm_body[j]  a.k.a. wtf is M?
## if normalize=True, then the output of the dot product is the cosine similarity between the two samples
p_lstm = dot([lstm_body, lstm_claim], axes=(2, 1), normalize=True)
p_lstm = Activation('softmax')(p_lstm)  # shape: (samples, n_pars)
p_lstm = Reshape((n_pars, 1))(p_lstm)   # shape: (samples, n_pars, 1)

print('p_lstm', p_lstm.shape)  # (39977, 9)

### cnn_body = cnn_body * p_lstm (multiply??)
cnn_body = multiply([cnn_body, p_lstm])  # (multiply??)
#cnn_body = Reshape((n_pars, 3, 100))(cnn_body)
cnn_claim = Reshape((300,))(cnn_claim)
print('cnn_body', cnn_body.shape)
print('cnn_claim', cnn_claim.shape)

## p_cnn = cnn_claim.T x M' x cnn_body[j]  a.k.a. wtf is M'?
## if normalize=True, then the output of the dot product is the cosine similarity between the two samples
p_cnn = dot([cnn_body, cnn_claim], axes=(2, 1), normalize=True)
p_cnn = Activation('softmax')(p_cnn)  # shape: (samples, body_size, claim_size)
print('p_cnn', p_cnn.shape)

## o = [mean(cnn_body); [max(p_cnn); mean(p_cnn)]; [max(p_lstm); mean(p_lstm)]; [max(p_tfidf); mean(p_tfidf)]]
output = [ K.mean(cnn_body, axis=2),
          [K.max(p_cnn, axis=1), K.mean(p_cnn, axis=1)],
          [K.max(p_lstm, axis=1), K.mean(p_lstm, axis=1)],
          [K.max(input_p_tfidf, axis=1), K.mean(input_p_tfidf, axis=1)] ] # ???

print('output', output.shape)

response = concatenate([output, lstm_claim, cnn_claim])
print('response layer:', response.shape)

stance = Dense(128, activation='relu')(response)
preds = Dense(output_size, activation='softmax')(stance)


# build the model
model = Model([input_body, input_claim, input_p_tfidf], preds)
model.compile(optimizer='rmsprop',
              loss='sparse_categorical_crossentropy',
              metrics=['accuracy'])


# train
model.fit([train_body, train_claim, train_p_tfidf], train_labels,
          batch_size=batch_size,
          epochs=epochs,
          validation_data=([val_body, val_claim, val_p_tfidf], val_labels))

# print model summary
print(model.summary())
