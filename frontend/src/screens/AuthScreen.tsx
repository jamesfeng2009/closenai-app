/**
 * 认证统一入口（登录 / 注册 / 邮箱验证码登录 / Apple 登录）。
 *
 * 游客可浏览 Tabs，写操作拦截跳此屏。底部「游客浏览」可跳过回 Tabs。
 * - login：邮箱/用户名 + 密码
 * - register：邮箱 + 用户名 + 密码 + 确认密码
 * - code：邮箱 + 验证码（未注册自动建账号；需先点「发送验证码」）
 * - Apple：expo-apple-authentication 拿 identityToken → 后端验签（仅 iOS）
 * 忘记密码 → navigate('ForgotPassword')
 */
import React, { useState, useRef, useEffect } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet, ScrollView, SafeAreaView,
  Keyboard, Alert, Platform,
} from 'react-native';
import * as AppleAuthentication from 'expo-apple-authentication';
import { useTranslation } from 'react-i18next';
import { useNavigation } from '@react-navigation/native';

import { Colors, Spacing, FontSize, BorderRadius } from '../theme/colors';
import {
  register, login, sendEmailCode, loginWithEmailCode, appleLoginApi, ApiError,
} from '../services/api';
import { useAuth } from '../context/AuthContext';
import type { CodePurpose } from '../types';

type Mode = 'login' | 'register' | 'code';

// 与后端 auth_service.PASSWORD_RE 一致：≥8 位且含字母+数字（本地预校验，省一次往返）
const PASSWORD_RE = /^(?=.*[A-Za-z])(?=.*\d).{8,}$/;

export default function AuthScreen() {
  const { t, i18n } = useTranslation();
  const navigation = useNavigation<any>();
  const { login: doLogin } = useAuth();

  const [mode, setMode] = useState<Mode>('login');
  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [code, setCode] = useState('');
  const [loading, setLoading] = useState(false);

  // 验证码发送冷却（60s）
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

  // ── 发送验证码（register/login/reset 用途；本屏 code 模式用 login purpose）──
  const onSendCode = async () => {
    if (!email.trim()) {
      showError(t('auth.emailRequired'));
      return;
    }
    // code 模式登录用 purpose=login（未注册自动建账号由后端 login-code 处理）
    const purpose: CodePurpose = mode === 'register' ? 'register' : 'login';
    try {
      setLoading(true);
      await sendEmailCode(email.trim(), purpose, (i18n.language || 'en').slice(0, 2));
      startCooldown();
      Alert.alert(t('auth.codeSentTitle'), t('auth.codeSentMessage'));
    } catch (e) {
      showError(e instanceof ApiError ? e.message : t('errors.sendCodeFailed'));
    } finally {
      setLoading(false);
    }
  };

  // ── 提交 ──
  const onSubmit = async () => {
    Keyboard.dismiss();
    if (loading) return;

    try {
      setLoading(true);
      if (mode === 'login') {
        if (!identifier.trim() || !password) {
          showError(t('auth.fillAllFields'));
          return;
        }
        const resp = await login(identifier.trim(), password);
        doLogin(resp);
      } else if (mode === 'register') {
        if (!email.trim() || !username.trim() || !password) {
          showError(t('auth.fillAllFields'));
          return;
        }
        if (!PASSWORD_RE.test(password)) {
          showError(t('auth.passwordWeak'));
          return;
        }
        if (password !== confirmPassword) {
          showError(t('auth.passwordMismatch'));
          return;
        }
        const resp = await register(email.trim(), username.trim(), password);
        doLogin(resp);
      } else {
        // code 模式
        if (!email.trim() || !code.trim()) {
          showError(t('auth.fillAllFields'));
          return;
        }
        const resp = await loginWithEmailCode(email.trim(), code.trim());
        doLogin(resp);
      }
      // 登录成功：回 Tabs（navigation.goBack 或 navigate Tabs）
      navigation.navigate('Tabs');
    } catch (e) {
      showError(e instanceof ApiError ? e.message : t('errors.requestFailed'));
    } finally {
      setLoading(false);
    }
  };

  // ── Apple 登录（仅 iOS；expo-apple-authentication 在 Android 不可用）──
  const onAppleLogin = async () => {
    if (loading) return;
    try {
      setLoading(true);
      const credential = await AppleAuthentication.signInAsync({
        requestedScopes: [
          AppleAuthentication.AppleAuthenticationScope.EMAIL,
          AppleAuthentication.AppleAuthenticationScope.FULL_NAME,
        ],
      });
      if (!credential.identityToken) {
        showError(t('errors.appleFailed'));
        return;
      }
      const resp = await appleLoginApi(credential.identityToken);
      doLogin(resp);
      navigation.navigate('Tabs');
    } catch (e: any) {
      if (e?.code === 'ERR_CANCELED') return; // 用户取消，静默
      showError(e instanceof ApiError ? e.message : t('errors.appleFailed'));
    } finally {
      setLoading(false);
    }
  };

  const switchMode = (m: Mode) => {
    setMode(m);
    setPassword('');
    setConfirmPassword('');
    setCode('');
  };

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Text style={styles.appName}>ClosenAI</Text>
        <Text style={styles.subtitle}>{t('auth.subtitle')}</Text>

        {/* Apple 登录（仅 iOS） */}
        {Platform.OS === 'ios' && (
          <AppleAuthentication.AppleAuthenticationButton
            buttonType={AppleAuthentication.AppleAuthenticationButtonType.SIGN_IN}
            buttonStyle={AppleAuthentication.AppleAuthenticationButtonStyle.BLACK}
            cornerRadius={BorderRadius.pill}
            style={styles.appleBtn}
            onPress={onAppleLogin}
          />
        )}

        {/* 模式切换 */}
        <View style={styles.modeTabs}>
          {([
            { key: 'login' as Mode, label: t('auth.login') },
            { key: 'register' as Mode, label: t('auth.register') },
            { key: 'code' as Mode, label: t('auth.codeLogin') },
          ]).map((tab) => (
            <TouchableOpacity
              key={tab.key}
              style={[styles.modeTab, mode === tab.key && styles.modeTabActive]}
              onPress={() => switchMode(tab.key)}
            >
              <Text style={[styles.modeTabText, mode === tab.key && styles.modeTabTextActive]}>
                {tab.label}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* 表单 */}
        {mode === 'login' && (
          <>
            <TextInput
              style={styles.input}
              placeholder={t('auth.identifierPlaceholder')}
              placeholderTextColor={Colors.textTertiary}
              value={identifier}
              onChangeText={setIdentifier}
              autoCapitalize="none"
              autoCorrect={false}
              returnKeyType="next"
            />
            <TextInput
              style={styles.input}
              placeholder={t('auth.passwordPlaceholder')}
              placeholderTextColor={Colors.textTertiary}
              value={password}
              onChangeText={setPassword}
              secureTextEntry
              autoCapitalize="none"
              autoCorrect={false}
            />
            <TouchableOpacity
              style={styles.forgotLink}
              onPress={() => navigation.navigate('ForgotPassword')}
            >
              <Text style={styles.forgotText}>{t('auth.forgotPassword')}</Text>
            </TouchableOpacity>
          </>
        )}

        {mode === 'register' && (
          <>
            <TextInput
              style={styles.input}
              placeholder={t('auth.emailPlaceholder')}
              placeholderTextColor={Colors.textTertiary}
              value={email}
              onChangeText={setEmail}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="email-address"
              returnKeyType="next"
            />
            <TextInput
              style={styles.input}
              placeholder={t('auth.usernamePlaceholder')}
              placeholderTextColor={Colors.textTertiary}
              value={username}
              onChangeText={setUsername}
              autoCapitalize="none"
              autoCorrect={false}
              returnKeyType="next"
            />
            <TextInput
              style={styles.input}
              placeholder={t('auth.passwordPlaceholder')}
              placeholderTextColor={Colors.textTertiary}
              value={password}
              onChangeText={setPassword}
              secureTextEntry
              autoCapitalize="none"
              autoCorrect={false}
              returnKeyType="next"
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
          </>
        )}

        {mode === 'code' && (
          <>
            <View style={styles.codeRow}>
              <TextInput
                style={[styles.input, { flex: 1 }]}
                placeholder={t('auth.emailPlaceholder')}
                placeholderTextColor={Colors.textTertiary}
                value={email}
                onChangeText={setEmail}
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="email-address"
                returnKeyType="next"
              />
              <TouchableOpacity
                style={[styles.sendCodeBtn, cooldown > 0 && styles.sendCodeBtnDisabled]}
                disabled={cooldown > 0 || loading}
                onPress={onSendCode}
              >
                <Text style={styles.sendCodeText}>
                  {cooldown > 0 ? t('auth.resendCode', { seconds: cooldown }) : t('auth.sendCode')}
                </Text>
              </TouchableOpacity>
            </View>
            <TextInput
              style={styles.input}
              placeholder={t('auth.codePlaceholder')}
              placeholderTextColor={Colors.textTertiary}
              value={code}
              onChangeText={setCode}
              keyboardType="number-pad"
              maxLength={8}
              autoCapitalize="none"
              autoCorrect={false}
            />
          </>
        )}

        {/* 提交 */}
        <TouchableOpacity
          style={[styles.primaryBtn, loading && styles.primaryBtnDisabled]}
          disabled={loading}
          onPress={onSubmit}
        >
          <Text style={styles.primaryBtnText}>
            {loading ? t('common.loading') : mode === 'login' ? t('auth.login') : mode === 'register' ? t('auth.register') : t('auth.login')}
          </Text>
        </TouchableOpacity>

        {/* 游客浏览 */}
        <TouchableOpacity style={styles.guestBtn} onPress={() => navigation.navigate('Tabs')}>
          <Text style={styles.guestText}>{t('auth.guestBrowse')}</Text>
        </TouchableOpacity>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.bgPrimary },
  content: { padding: Spacing.xl, paddingBottom: 48 },
  appName: {
    fontSize: 28, fontWeight: '700', color: Colors.gold400,
    textAlign: 'center', marginTop: Spacing.lg,
  },
  subtitle: {
    fontSize: FontSize.lg, color: Colors.textSecondary,
    textAlign: 'center', marginTop: Spacing.sm, marginBottom: Spacing.xl,
  },
  appleBtn: { width: '100%', height: 50, marginBottom: Spacing.lg },
  modeTabs: {
    flexDirection: 'row', backgroundColor: Colors.bgSecondary,
    borderRadius: BorderRadius.pill, padding: 4, marginBottom: Spacing.xl,
  },
  modeTab: { flex: 1, paddingVertical: 8, alignItems: 'center', borderRadius: BorderRadius.pill },
  modeTabActive: { backgroundColor: Colors.gold400 },
  modeTabText: { fontSize: FontSize.xl, color: Colors.textSecondary, fontWeight: '600' },
  modeTabTextActive: { color: '#050508' },
  input: {
    borderWidth: 1, borderColor: Colors.borderLight, borderRadius: BorderRadius.lg,
    paddingVertical: 14, paddingHorizontal: Spacing.lg, marginBottom: Spacing.md,
    color: Colors.textPrimary, fontSize: FontSize.title,
  },
  codeRow: { flexDirection: 'row', gap: Spacing.sm, marginBottom: Spacing.md },
  sendCodeBtn: {
    backgroundColor: Colors.bgTertiary, borderRadius: BorderRadius.lg,
    paddingHorizontal: Spacing.lg, justifyContent: 'center', alignItems: 'center',
    borderWidth: 1, borderColor: Colors.borderGold,
  },
  sendCodeBtnDisabled: { opacity: 0.5 },
  sendCodeText: { fontSize: FontSize.md, color: Colors.gold400, fontWeight: '600' },
  forgotLink: { alignSelf: 'flex-end', marginBottom: Spacing.lg },
  forgotText: { fontSize: FontSize.md, color: Colors.gold400 },
  primaryBtn: {
    backgroundColor: Colors.gold400, borderRadius: BorderRadius.pill,
    paddingVertical: 14, alignItems: 'center', marginTop: Spacing.sm, marginBottom: Spacing.md,
  },
  primaryBtnDisabled: { opacity: 0.5 },
  primaryBtnText: { fontSize: FontSize.xl, fontWeight: '700', color: '#050508' },
  guestBtn: { alignItems: 'center', paddingVertical: 12, marginTop: Spacing.sm },
  guestText: { fontSize: FontSize.md, color: Colors.textTertiary },
});
