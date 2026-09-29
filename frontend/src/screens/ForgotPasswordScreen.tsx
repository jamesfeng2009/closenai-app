/**
 * 忘记密码（2 步重置）。
 *
 * Step 1 — 输入邮箱 → 发送重置验证码（forgot-password 防枚举：无论邮箱是否注册统一返回成功）
 * Step 2 — 输入验证码 + 新密码 → 重置（reset-password 验码 + 改密 + 吊销全部 refresh）
 * 成功后提示并回登录页
 */
import React, { useState, useRef, useEffect } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet, ScrollView, SafeAreaView,
  KeyboardAvoidingView, Alert, Keyboard, Platform,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import { useNavigation } from '@react-navigation/native';

import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import { forgotPassword, resetPassword, ApiError } from '../services/api';

export default function ForgotPasswordScreen() {
  const { t } = useTranslation();
  const navigation = useNavigation<any>();

  const [step, setStep] = useState<1 | 2>(1);
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);

  // 发送冷却
  const [cooldown, setCooldown] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  const startCooldown = () => {
    setCooldown(60);
    timerRef.current = setInterval(() => {
      setCooldown((s) => {
        if (s <= 1) {
          if (timerRef.current) clearInterval(timerRef.current);
          return 0;
        }
        return s - 1;
      });
    }, 1000);
  };

  const showError = (msg: string) => Alert.alert(t('common.error'), msg);

  // Step 1：发送重置验证码
  const onSendCode = async () => {
    if (!email.trim()) {
      showError(t('auth.emailRequired'));
      return;
    }
    try {
      setLoading(true);
      await forgotPassword(email.trim());
      startCooldown();
      Alert.alert(t('auth.codeSentTitle'), t('auth.codeSentMessage'));
      setStep(2);
    } catch (e) {
      showError(e instanceof ApiError ? e.message : t('errors.sendCodeFailed'));
    } finally {
      setLoading(false);
    }
  };

  // Step 2：重置密码
  const onReset = async () => {
    Keyboard.dismiss();
    if (loading) return;
    if (!code.trim() || !newPassword) {
      showError(t('auth.fillAllFields'));
      return;
    }
    if (newPassword !== confirmPassword) {
      showError(t('auth.passwordMismatch'));
      return;
    }
    try {
      setLoading(true);
      await resetPassword(email.trim(), code.trim(), newPassword);
      Alert.alert(t('auth.resetSuccessTitle'), t('auth.resetSuccessMessage'), [
        { text: t('auth.login'), onPress: () => navigation.navigate('Auth') },
      ]);
    } catch (e) {
      showError(e instanceof ApiError ? e.message : t('errors.requestFailed'));
    } finally {
      setLoading(false);
    }
  };

  const resendCode = async () => {
    if (cooldown > 0) return;
    try {
      await forgotPassword(email.trim());
      startCooldown();
      Alert.alert(t('auth.codeSentTitle'), t('auth.codeSentMessage'));
    } catch (e) {
      showError(e instanceof ApiError ? e.message : t('errors.sendCodeFailed'));
    }
  };

  return (
    <SafeAreaView style={styles.container}>
      {/* iOS：验证码 + 两次密码输入都在屏幕下半部，键盘弹出会盖住下一步按钮 */}
      <KeyboardAvoidingView
        style={styles.kav}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Text style={styles.title}>{t('auth.resetPassword')}</Text>
        <Text style={styles.stepText}>
          {step === 1 ? t('auth.resetStep1') : t('auth.resetStep2')}
        </Text>

        <TextInput
          style={[styles.input, step === 2 && styles.inputLocked]}
          placeholder={t('auth.emailPlaceholder')}
          placeholderTextColor={Colors.textTertiary}
          value={email}
          onChangeText={setEmail}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="email-address"
          editable={step === 1}
        />

        {step === 1 ? (
          <>
            <TouchableOpacity
              style={[styles.primaryBtn, loading && styles.primaryBtnDisabled]}
              disabled={loading}
              onPress={onSendCode}
            >
              <Text style={styles.primaryBtnText}>
                {loading ? t('common.loading') : t('auth.sendCode')}
              </Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.backBtn} onPress={() => navigation.goBack()}>
              <Text style={styles.backText}>{t('auth.backToLogin')}</Text>
            </TouchableOpacity>
          </>
        ) : (
          <>
            <View style={styles.codeRow}>
              <TextInput
                style={[styles.input, { flex: 1 }]}
                placeholder={t('auth.codePlaceholder')}
                placeholderTextColor={Colors.textTertiary}
                value={code}
                onChangeText={setCode}
                keyboardType="number-pad"
                maxLength={8}
                autoCapitalize="none"
                autoCorrect={false}
              />
              <TouchableOpacity
                style={[styles.sendCodeBtn, cooldown > 0 && styles.sendCodeBtnDisabled]}
                disabled={cooldown > 0}
                onPress={resendCode}
              >
                <Text style={styles.sendCodeText}>
                  {cooldown > 0 ? t('auth.resendCode', { seconds: cooldown }) : t('auth.resend')}
                </Text>
              </TouchableOpacity>
            </View>
            <TextInput
              style={styles.input}
              placeholder={t('auth.passwordPlaceholder')}
              placeholderTextColor={Colors.textTertiary}
              value={newPassword}
              onChangeText={setNewPassword}
              secureTextEntry
              autoCapitalize="none"
              autoCorrect={false}
            />
            <TextInput
              style={styles.input}
              placeholder={t('auth.confirmPassword')}
              placeholderTextColor={Colors.textTertiary}
              value={confirmPassword}
              onChangeText={setConfirmPassword}
              secureTextEntry
              autoCapitalize="none"
              autoCorrect={false}
            />
            <TouchableOpacity
              style={[styles.primaryBtn, loading && styles.primaryBtnDisabled]}
              disabled={loading}
              onPress={onReset}
            >
              <Text style={styles.primaryBtnText}>
                {loading ? t('common.loading') : t('auth.confirmReset')}
              </Text>
            </TouchableOpacity>
          </>
        )}
      </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  kav: { flex: 1 },
  content: { padding: Spacing.xl, paddingBottom: 48 },
  title: {
    fontSize: 22, fontWeight: '700', color: Colors.textPrimary,
    textAlign: 'center', marginTop: Spacing.lg,
  },
  stepText: {
    fontSize: FontSize.lg, color: Colors.textSecondary,
    textAlign: 'center', marginTop: Spacing.sm, marginBottom: Spacing.xl,
  },
  input: {
    borderWidth: 1, borderColor: Colors.borderLight, borderRadius: BorderRadius.lg,
    paddingVertical: 14, paddingHorizontal: Spacing.lg, marginBottom: Spacing.md,
    color: Colors.textPrimary, fontSize: FontSize.title,
  },
  inputLocked: { opacity: 0.6 },
  codeRow: { flexDirection: 'row', gap: Spacing.sm, marginBottom: Spacing.md },
  sendCodeBtn: {
    backgroundColor: Colors.bgTertiary, borderRadius: BorderRadius.lg,
    paddingHorizontal: Spacing.lg, justifyContent: 'center', alignItems: 'center',
    borderWidth: 1, borderColor: Colors.borderGold,
  },
  sendCodeBtnDisabled: { opacity: 0.5 },
  sendCodeText: { fontSize: FontSize.md, color: Colors.gold400, fontWeight: '600' },
  primaryBtn: {
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.pill,
    paddingVertical: 14, alignItems: 'center', marginTop: Spacing.sm, marginBottom: Spacing.md,
  },
  primaryBtnDisabled: { opacity: 0.5 },
  primaryBtnText: { fontSize: FontSize.xl, fontWeight: '700', color: '#050508' },
  backBtn: { alignItems: 'center', paddingVertical: 12 },
  backText: { fontSize: FontSize.md, color: Colors.textTertiary },
});
